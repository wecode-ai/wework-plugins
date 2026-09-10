// SPDX-License-Identifier: MIT
package main

import (
	"context"
	"crypto/tls"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	ext "github.com/larksuite/cli/extension/credential"
	"github.com/larksuite/cli/internal/auth"
	"github.com/larksuite/cli/internal/core"
	"github.com/larksuite/cli/internal/keychain"
	pluginauth "github.com/larksuite/cli/internal/wegentpluginauth"
)

func TestOriginalBusinessCommandUsesMemoryAndNeverKeychain(t *testing.T) {
	_, c := fixture(t)
	delete(c, "refresh_token")
	delete(c, "provider_private")
	count := 0
	businessSeen := false
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		count++
		if r.URL.Path == "/open-apis/calendar/v4/calendars" {
			businessSeen = true
		}
		w.Header().Set("Content-Type", "application/json")
		if (r.URL.Path == auth.PathUserInfoV1 || r.URL.Path == "/open-apis/calendar/v4/calendars") && r.Header.Get("Authorization") != "Bearer synthetic-access" {
			t.Errorf("missing memory credential on %s", r.URL.Path)
		}
		if r.URL.Path == auth.PathUserInfoV1 {
			io.WriteString(w, `{"code":0,"data":{"open_id":"ou_synthetic"}}`)
		} else {
			io.WriteString(w, `{"code":0,"data":{"items":[]}}`)
		}
	}))
	defer server.Close()
	old := newBusinessTransport
	newBusinessTransport = func(host string) *http.Transport {
		return &http.Transport{Proxy: func(req *http.Request) (*url.URL, error) { return credentialOrigin(host, req) }, DialContext: func(ctx context.Context, network, address string) (net.Conn, error) {
			return (&net.Dialer{}).DialContext(ctx, network, server.Listener.Addr().String())
		}, TLSClientConfig: &tls.Config{InsecureSkipVerify: true}} // Synthetic loopback server only.
	}
	defer func() { newBusinessTransport = old }()
	keychain.WegentAccess = noKeychain{}
	if err := runUser(context.Background(), c, []string{"api", "GET", "/open-apis/calendar/v4/calendars", "--as", "user"}); err != nil {
		t.Fatal(err)
	}
	if count < 2 || !businessSeen {
		t.Fatal("original CLI did not verify identity and execute business request")
	}
}

type memoryKeys struct {
	values          map[string]string
	failAfterRemove bool
	removes         int
}

func (k *memoryKeys) Get(_, key string) (string, error) {
	value, ok := k.values[key]
	if !ok {
		return "", keychain.ErrNotFound
	}
	return value, nil
}
func (k *memoryKeys) Set(_, key, value string) error { k.values[key] = value; return nil }
func (k *memoryKeys) Remove(_, key string) error {
	delete(k.values, key)
	k.removes++
	if k.failAfterRemove {
		return errors.New("synthetic failure")
	}
	return nil
}

type roundTrip func(*http.Request) (*http.Response, error)

func (f roundTrip) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }
func response(body string) *http.Response {
	return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(body)), Header: http.Header{}}
}

func fixture(t *testing.T) (*memoryKeys, pluginauth.Credential) {
	t.Helper()
	root := t.TempDir()
	t.Setenv("LARKSUITE_CLI_CONFIG_DIR", root)
	t.Setenv("WEGENT_LARK_STATE_DIR", filepath.Join(root, "state"))
	core.SetCurrentWorkspace(core.WorkspaceLocal)
	keys := &memoryKeys{values: map[string]string{}}
	previous := keychain.WegentAccess
	keychain.WegentAccess = keys
	t.Cleanup(func() { keychain.WegentAccess = previous })
	cfg := &core.MultiAppConfig{CurrentApp: "cli_synthetic", Apps: []core.AppConfig{{AppId: "cli_synthetic", AppSecret: core.PlainSecret("synthetic-app-secret"), Brand: core.BrandFeishu, Users: []core.AppUser{{UserOpenId: "ou_synthetic"}}}}}
	if err := core.SaveMultiAppConfig(cfg); err != nil {
		t.Fatal(err)
	}
	token := &auth.StoredUAToken{AppId: "cli_synthetic", UserOpenId: "ou_synthetic", AccessToken: "synthetic-access", RefreshToken: "synthetic-refresh", ExpiresAt: time.Now().Add(time.Hour).UnixMilli(), RefreshExpiresAt: time.Now().Add(24 * time.Hour).UnixMilli(), Scope: "contact:user.base:readonly"}
	data, _ := json.Marshal(token)
	keys.values["cli_synthetic:ou_synthetic"] = string(data)
	oldClient := client
	client = &http.Client{Transport: roundTrip(func(req *http.Request) (*http.Response, error) {
		if req.URL.Host != "open.feishu.cn" || req.Header.Get("Authorization") != "Bearer synthetic-access" {
			t.Errorf("unexpected provider request")
		}
		return response(`{"code":0,"data":{"open_id":"ou_synthetic"}}`), nil
	})}
	t.Cleanup(func() { client = oldClient })
	account, err := exportUser(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	return keys, account.Credential
}
func TestExportDoesNotMutateAndKeepsRefreshSecretsPrivate(t *testing.T) {
	keys, c := fixture(t)
	if keys.removes != 0 || c["refresh_token"] != "synthetic-refresh" {
		t.Fatal("unexpected source mutation")
	}
	if c["app_secret"] != nil || c["provider_private"].(map[string]any)["app_secret"] != "synthetic-app-secret" {
		t.Fatal("wrong secret boundary")
	}
}
func TestDetachRecoveryAndReplay(t *testing.T) {
	keys, c := fixture(t)
	id := strings.Repeat("a", 64)
	keys.failAfterRemove = true
	if detach(context.Background(), id, c) == nil {
		t.Fatal("cleanup interruption must fail")
	}
	keys.failAfterRemove = false
	if err := detach(context.Background(), id, c); err != nil {
		t.Fatal(err)
	}
	if err := detach(context.Background(), id, c); err != nil {
		t.Fatal(err)
	}
	if keys.removes != 1 {
		t.Fatal("replay repeated deletion")
	}
	root, _ := stateRoot()
	files, _ := os.ReadDir(root)
	for _, file := range files {
		data, _ := os.ReadFile(filepath.Join(root, file.Name()))
		if strings.Contains(string(data), "synthetic-access") || strings.Contains(string(data), "synthetic-refresh") || strings.Contains(string(data), "synthetic-app-secret") {
			t.Fatal("secret in receipt")
		}
	}
}
func TestChangedSourceAbortsOldHandoffDurably(t *testing.T) {
	keys, c := fixture(t)
	old := keys.values["cli_synthetic:ou_synthetic"]
	keys.values["cli_synthetic:ou_synthetic"] = strings.ReplaceAll(old, "synthetic-access", "new-access")
	id := strings.Repeat("b", 64)
	if !errors.Is(detach(context.Background(), id, c), pluginauth.ErrSourceChanged) {
		t.Fatal("expected source changed")
	}
	keys.values["cli_synthetic:ou_synthetic"] = old
	if !errors.Is(detach(context.Background(), id, c), pluginauth.ErrSourceChanged) || keys.removes != 0 {
		t.Fatal("old transfer can remove new login")
	}
}
func TestCorruptSourceIsNotTreatedAsMissing(t *testing.T) {
	keys, c := fixture(t)
	keys.values["cli_synthetic:ou_synthetic"] = "not-json"
	if detach(context.Background(), strings.Repeat("c", 64), c) == nil || keys.removes != 0 {
		t.Fatal("corrupt source accepted")
	}
}
func TestRefreshIsSingleRequestAndDoesNotWriteSource(t *testing.T) {
	keys, c := fixture(t)
	before := keys.values["cli_synthetic:ou_synthetic"]
	count := 0
	client = &http.Client{Transport: roundTrip(func(req *http.Request) (*http.Response, error) {
		count++
		body, _ := io.ReadAll(req.Body)
		values, _ := url.ParseQuery(string(body))
		if req.URL.String() != "https://open.feishu.cn/open-apis/authen/v2/oauth/token" || values.Get("client_secret") != "synthetic-app-secret" || values.Get("refresh_token") != "synthetic-refresh" {
			t.Fatal("incorrect refresh request")
		}
		return response(`{"access_token":"new-access","refresh_token":"new-refresh","expires_in":3600}`), nil
	})}
	next, err := refresh(context.Background(), c)
	if err != nil || count != 1 || next.Credential["refresh_token"] != "new-refresh" || keys.values["cli_synthetic:ou_synthetic"] != before {
		t.Fatal("refresh contract")
	}
	count = 0
	client.Transport = roundTrip(func(*http.Request) (*http.Response, error) { count++; return nil, errors.New("synthetic secret body") })
	if _, err = refresh(context.Background(), c); err == nil || count != 1 {
		t.Fatal("uncertain refresh retried")
	}
}
func TestUserAndBotProvidersCannotCrossIdentities(t *testing.T) {
	_, c := fixture(t)
	for _, bot := range []bool{false, true} {
		p := &memoryProvider{c, "synthetic-memory-token", bot}
		account, err := p.ResolveAccount(context.Background())
		if err != nil || account.AppSecret != "" {
			t.Fatal("app secret supplied to business tree")
		}
		correct, wrong := ext.TokenTypeUAT, ext.TokenTypeTAT
		if bot {
			correct, wrong = wrong, correct
		}
		if token, err := p.ResolveToken(context.Background(), ext.TokenSpec{Type: correct, AppID: "cli_synthetic"}); err != nil || token.Value != "synthetic-memory-token" {
			t.Fatal("missing memory token")
		}
		if _, err := p.ResolveToken(context.Background(), ext.TokenSpec{Type: wrong}); err == nil {
			t.Fatal("identity escalation")
		}
	}
}
func TestManagedRunRefusesManagementSecretsAndCommands(t *testing.T) {
	_, c := fixture(t)
	if runUser(context.Background(), c, []string{"docs", "--help"}) == nil {
		t.Fatal("refresh secret accepted")
	}
	for _, args := range [][]string{{"auth", "login"}, {"config", "show"}, {"docs", "--profile=other"}, {"api", "--base-url=https://outside.invalid"}, {"im", "--debug"}} {
		if allowed(args) {
			t.Fatalf("unsafe arguments: %v", args)
		}
	}
	if !allowed([]string{"im", "--help"}) || !allowed([]string{"im", "messages", "create", "--yes"}) {
		t.Fatal("ordinary commands or explicit confirmation lost")
	}
}
func TestOriginGuardSurvivesTransportCloning(t *testing.T) {
	transport := &http.Transport{Proxy: func(req *http.Request) (*url.URL, error) { return credentialOrigin("open.feishu.cn", req) }}
	clone := transport.Clone()
	for _, target := range []string{"https://outside.invalid", "http://open.feishu.cn", "https://open.feishu.cn.evil.invalid"} {
		req, _ := http.NewRequest("GET", target, nil)
		req.Header.Set("Authorization", "Bearer synthetic")
		if _, err := clone.Proxy(req); err == nil {
			t.Fatal("credential origin accepted")
		}
	}
	req, _ := http.NewRequest("GET", "https://open.feishu.cn/open-apis", nil)
	req.Header.Set("Authorization", "Bearer synthetic")
	if _, err := clone.Proxy(req); err != nil {
		t.Fatal(err)
	}
}
func TestRevokeUsesPrivateClientSecretAndRefreshToken(t *testing.T) {
	_, c := fixture(t)
	count := 0
	client = &http.Client{Transport: roundTrip(func(req *http.Request) (*http.Response, error) {
		count++
		body, _ := io.ReadAll(req.Body)
		fields, _ := url.ParseQuery(string(body))
		if req.URL.Host != "accounts.feishu.cn" || fields.Get("token_type_hint") != "refresh_token" || fields.Get("client_secret") != "synthetic-app-secret" {
			t.Fatal("wrong revoke request")
		}
		return response(`{}`), nil
	})}
	if revoke(context.Background(), c) != nil || count != 1 {
		t.Fatal("revoke failed")
	}
}

func TestLocalLogoutPreservesOtherUsers(t *testing.T) {
	keys, _ := fixture(t)
	cfg, err := core.LoadMultiAppConfig()
	if err != nil {
		t.Fatal(err)
	}
	cfg.Apps[0].Users = append(cfg.Apps[0].Users, core.AppUser{UserOpenId: "ou_other"})
	if err := core.SaveMultiAppConfig(cfg); err != nil {
		t.Fatal(err)
	}
	keys.values["cli_synthetic:ou_other"] = "unrelated"
	if err := clearLocal(); err != nil {
		t.Fatal(err)
	}
	cfg, err = core.LoadMultiAppConfig()
	if err != nil || len(cfg.Apps[0].Users) != 1 || keys.values["cli_synthetic:ou_other"] != "unrelated" {
		t.Fatal("removed unrelated user")
	}
}
