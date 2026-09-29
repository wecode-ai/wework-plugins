// SPDX-License-Identifier: Apache-2.0
package main

import (
	"context"
	"encoding/json"
	"math"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/DingTalk-Real-AI/dingtalk-workspace-cli/internal/auth"
	pluginauth "github.com/DingTalk-Real-AI/dingtalk-workspace-cli/internal/wegentpluginauth"
	"github.com/DingTalk-Real-AI/dingtalk-workspace-cli/pkg/config"
	"github.com/DingTalk-Real-AI/dingtalk-workspace-cli/pkg/edition"
)

func fixture() pluginauth.Credential {
	// The real private transport uses Decoder.UseNumber, not float64.
	return pluginauth.Credential{"token": "synthetic-old-access", "expires_at": json.Number(strconv.FormatInt(time.Now().Add(time.Hour).Unix(), 10)),
		"source": "mcp", "client_id": "synthetic-client", "corp_id": "corp", "user_id": "alice"}
}

func TestSnapshotWhitelistNeverExportsPrivateGrants(t *testing.T) {
	token, err := decode(fixture())
	if err != nil {
		t.Fatal(err)
	}
	token.RefreshToken, token.PersistentCode = "synthetic-refresh", "synthetic-private"
	token.RefreshExpAt = time.Now().Add(24 * time.Hour)
	account, err := encode(token)
	if err != nil || account.ID != "corp:alice" || len(account.Credential) != 6 {
		t.Fatal("wrong snapshot")
	}
	data, _ := json.Marshal(account.Credential)
	if strings.Contains(string(data), "synthetic-refresh") || strings.Contains(string(data), "synthetic-private") {
		t.Fatal("export leaked a renewable credential")
	}
	for _, key := range []string{"refresh_token", "provider_private", "persistent_code", "client_secret", "access_token", "unexpected"} {
		value := fixture()
		value[key] = "synthetic-private"
		if _, err := decode(value); err == nil {
			t.Fatalf("accepted private/unknown field %s", key)
		}
	}
}

func TestSnapshotRequiresIdentityAndFiniteIntegerExpiry(t *testing.T) {
	for _, key := range []string{"token", "corp_id", "user_id", "client_id", "source", "expires_at"} {
		value := fixture()
		delete(value, key)
		if _, err := decode(value); err == nil {
			t.Fatalf("accepted missing %s", key)
		}
	}
	for _, expiry := range []any{nil, "123", true, 0, -1, math.NaN(), math.Inf(1), 1.5, 253402300800.0, json.Number("1.5"), json.Number("9223372036854775808")} {
		value := fixture()
		value["expires_at"] = expiry
		if _, err := decode(value); err == nil {
			t.Fatalf("accepted invalid expiry %v", expiry)
		}
	}
}

func TestExpiredCloudSnapshotNeverTouchesNetworkOrLocalState(t *testing.T) {
	original := http.DefaultTransport
	http.DefaultTransport = roundTrip(func(*http.Request) (*http.Response, error) {
		t.Fatal("expired snapshot contacted provider")
		return nil, denied
	})
	defer func() { http.DefaultTransport = original }()
	for _, expiry := range []int64{time.Now().Add(-time.Minute).Unix(), time.Now().Add(20 * time.Second).Unix()} {
		for _, args := range [][]string{{"account-status"}, {"todo", "task", "get", "--task-id", "synthetic"}} {
			value := fixture()
			value["expires_at"] = expiry
			output, err := capture(func() int {
				if run(context.Background(), value, args) != nil {
					return 1
				}
				return 0
			})
			var result map[string]any
			if err != nil || json.Unmarshal(output, &result) != nil || result["error"] != "dingtalk_cloud_token_expired" {
				t.Fatal("expiry was not a recoverable business result")
			}
		}
	}
}

func TestAccountHealthReturnsMetadataWithoutLocalAuthentication(t *testing.T) {
	credential := fixture()
	output, err := capture(func() int {
		if run(context.Background(), credential, []string{"account-status"}) != nil {
			return 1
		}
		return 0
	})
	if err != nil {
		t.Fatal(err)
	}
	var result map[string]any
	if json.Unmarshal(output, &result) != nil || result["authenticated"] != true || result["accountId"] != "corp:alice" || len(result) != 2 {
		t.Fatal("health must return only account metadata")
	}
}

func TestMemoryHooksNeverPersistCredentialsOrFallBackToKeychain(t *testing.T) {
	custom := t.TempDir()
	t.Setenv("DWS_CONFIG_DIR", custom)
	if err := os.WriteFile(filepath.Join(custom, "mcp_url"), []byte("https://custom.invalid"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := export(context.Background()); err == nil {
		t.Fatal("custom provider was exported as the official provider")
	}
	token, err := decode(fixture())
	if err != nil {
		t.Fatal(err)
	}
	err = withMemory(token, func(directory string) error {
		if config.GetMCPBaseURL() != config.DefaultMCPBaseURL {
			t.Fatal("business credential inherited a custom token endpoint")
		}
		loaded, err := auth.LoadTokenData(directory)
		if err != nil || loaded.AccessToken != token.AccessToken {
			t.Fatal("memory load failed")
		}
		if auth.SaveTokenData(directory, token) == nil {
			t.Fatal("business code persisted a credential")
		}
		if edition.Get().DeleteToken(directory) == nil {
			t.Fatal("business code deleted auth")
		}
		got, err := edition.Get().TokenProvider(context.Background(), func() (string, error) { t.Fatal("used local token fallback"); return "", nil })
		if err != nil || got != token.AccessToken {
			t.Fatal("memory token failed")
		}
		return filepath.WalkDir(directory, func(path string, entry os.DirEntry, err error) error {
			if err != nil {
				return err
			}
			if !entry.IsDir() {
				contents, err := os.ReadFile(path)
				if err != nil {
					return err
				}
				if strings.Contains(string(contents), "synthetic-") {
					t.Fatal("secret written to auth directory")
				}
			}
			return nil
		})
	})
	if err != nil {
		t.Fatal(err)
	}
}

type roundTrip func(*http.Request) (*http.Response, error)

func (f roundTrip) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func TestBusinessDeniesAuthenticationAndPrivateGrantMaterial(t *testing.T) {
	product := edition.Get().VisibleProducts()[0]
	if !allowed([]string{product, "--help"}) {
		t.Fatal("known product help denied")
	}
	for _, args := range [][]string{{"auth", "login"}, {product, "--token=secret"}, {product, "--profile", "other"}, {product, "--debug"}, {product, "-o/tmp/out"}, {product, "-yv"}} {
		if allowed(args) {
			t.Fatal("accepted auth control override")
		}
	}
	value := fixture()
	value["refresh_token"] = "synthetic-refresh"
	if run(context.Background(), value, []string{product, "--help"}) == nil {
		t.Fatal("business received refresh token")
	}
}

func TestRealUpstreamProductHelpRunsWithoutLocalAuthentication(t *testing.T) {
	value := fixture()
	output, err := capture(func() int {
		if run(context.Background(), value, []string{edition.Get().VisibleProducts()[0], "--help"}) != nil {
			return 1
		}
		return 0
	})
	if err != nil || len(output) == 0 || strings.Contains(string(output), "synthetic-") {
		t.Fatal("upstream help failed or leaked credential")
	}
}

func TestRealUpstreamBusinessCommandUsesOnlyTheSuppliedAccessToken(t *testing.T) {
	// Trust only the isolated fixture; production retains DWS HTTPS/domain checks.
	t.Setenv("DWS_ALLOW_HTTP_ENDPOINTS", "1")
	t.Setenv("DWS_TRUSTED_DOMAINS", "127.0.0.1")
	calls := 0
	server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		var rpc map[string]any
		if json.NewDecoder(request.Body).Decode(&rpc) != nil {
			http.Error(response, "invalid RPC", 400)
			return
		}
		response.Header().Set("Content-Type", "application/json")
		var result any = map[string]any{}
		switch rpc["method"] {
		case "initialize":
			result = map[string]any{"protocolVersion": "2025-03-26", "capabilities": map[string]any{"tools": map[string]any{}}, "serverInfo": map[string]any{"name": "synthetic-provider", "version": "1"}}
		case "notifications/initialized":
			response.WriteHeader(202)
			return
		case "tools/call":
			calls++
			headers, _ := json.Marshal(request.Header)
			params, _ := json.Marshal(rpc["params"])
			if !strings.Contains(string(headers), "synthetic-old-access") {
				t.Error("business request omitted supplied access token")
			}
			if !strings.Contains(string(params), "synthetic-task") {
				t.Error("business request lost task input")
			}
			result = map[string]any{"content": []any{map[string]any{"type": "text", "text": `{"taskId":"synthetic-task","subject":"native-dws-account-read"}`}}}
		default:
			t.Errorf("unexpected RPC method %v", rpc["method"])
			http.Error(response, "unexpected", 400)
			return
		}
		json.NewEncoder(response).Encode(map[string]any{"jsonrpc": "2.0", "id": rpc["id"], "result": result})
	}))
	defer server.Close()
	original := edition.Get()
	hooks := *original
	hooks.StaticServers = func() []edition.ServerInfo {
		servers := original.StaticServers()
		for index := range servers {
			servers[index].Endpoint = server.URL
		}
		return servers
	}
	edition.Override(&hooks)
	defer edition.Override(original)
	value := fixture()
	value["expires_at"] = time.Now().Add(time.Hour).Unix()
	output, err := capture(func() int {
		if run(context.Background(), value, []string{"todo", "task", "get", "--task-id", "synthetic-task", "--format", "json"}) != nil {
			return 1
		}
		return 0
	})
	if err != nil {
		t.Fatal("upstream business command failed")
	}
	if calls != 1 || !strings.Contains(string(output), "native-dws-account-read") || strings.Contains(string(output), "synthetic-old-access") {
		t.Fatal("business did not use native auth safely")
	}
}
