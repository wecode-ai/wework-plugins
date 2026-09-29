// SPDX-License-Identifier: Apache-2.0
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"math"
	"os"
	"strings"
	"time"

	"github.com/DingTalk-Real-AI/dingtalk-workspace-cli/internal/app"
	"github.com/DingTalk-Real-AI/dingtalk-workspace-cli/internal/auth"
	pluginauth "github.com/DingTalk-Real-AI/dingtalk-workspace-cli/internal/wegentpluginauth"
	"github.com/DingTalk-Real-AI/dingtalk-workspace-cli/pkg/config"
	"github.com/DingTalk-Real-AI/dingtalk-workspace-cli/pkg/edition"
)

var denied = errors.New("plugin_auth_dws_operation_denied")

func export(ctx context.Context) (pluginauth.Account, error) {
	token, err := auth.SnapshotWegentToken(ctx, config.DefaultConfigDir())
	if err != nil {
		return pluginauth.Account{}, denied
	}
	return encode(token)
}

func allowed(arguments []string) bool {
	if len(arguments) == 1 && arguments[0] == "account-status" {
		return true
	}
	if len(arguments) == 0 {
		return false
	}
	products := edition.Get().VisibleProducts
	if products == nil {
		return false
	}
	found := false
	for _, product := range products() {
		if arguments[0] == product {
			found = true
			break
		}
	}
	if !found {
		return false
	}
	blocked := []string{"--token", "--client-id", "--client-secret", "--profile", "--debug", "--verbose", "-v", "--output", "-o", "--mock"}
	for _, argument := range arguments {
		name := strings.SplitN(argument, "=", 2)[0]
		if strings.HasPrefix(name, "-") && !strings.HasPrefix(name, "--") && len(name) > 2 && !strings.HasPrefix(name, "-f") && strings.ContainsAny(name[1:], "vo") {
			return false
		}
		for _, flag := range blocked {
			if name == flag || (len(flag) == 2 && strings.HasPrefix(name, flag)) {
				return false
			}
		}
	}
	return true
}

// Keep this whitelist symmetric with encode: newly added upstream secret fields
// must never accidentally become part of the cloud credential.
var snapshotFields = map[string]bool{
	"token": true, "expires_at": true, "corp_id": true, "user_id": true,
	"source": true, "client_id": true,
}

func decode(credential pluginauth.Credential) (*auth.TokenData, error) {
	for key := range credential {
		if !snapshotFields[key] {
			return nil, denied
		}
	}
	text := func(key string) string {
		value, _ := credential[key].(string)
		return value
	}
	var seconds float64
	switch value := credential["expires_at"].(type) {
	case json.Number:
		integer, err := value.Int64()
		if err != nil {
			return nil, denied
		}
		seconds = float64(integer)
	case float64:
		seconds = value
	case int64:
		seconds = float64(value)
	case int:
		seconds = float64(value)
	default:
		return nil, denied
	}
	if math.IsNaN(seconds) || math.IsInf(seconds, 0) || seconds <= 0 ||
		seconds > 253402300799 || seconds != math.Trunc(seconds) {
		return nil, denied
	}
	token := &auth.TokenData{
		AccessToken: text("token"), ExpiresAt: time.Unix(int64(seconds), 0),
		CorpID: text("corp_id"), UserID: text("user_id"),
		Source: text("source"), ClientID: text("client_id"),
	}
	if strings.TrimSpace(token.AccessToken) == "" || strings.TrimSpace(token.CorpID) == "" ||
		strings.TrimSpace(token.UserID) == "" || token.Source != "mcp" || strings.TrimSpace(token.ClientID) == "" {
		return nil, denied
	}
	return token, nil
}

func encode(token *auth.TokenData) (pluginauth.Account, error) {
	if token == nil {
		return pluginauth.Account{}, denied
	}
	value := pluginauth.Credential{
		"token": token.AccessToken, "expires_at": token.ExpiresAt.Unix(),
		"corp_id": token.CorpID, "user_id": token.UserID,
		"source": token.Source, "client_id": token.ClientID,
	}
	if _, err := decode(value); err != nil || !token.ExpiresAt.After(time.Now().Add(30*time.Second)) {
		return pluginauth.Account{}, denied
	}
	return pluginauth.Account{ID: token.CorpID + ":" + token.UserID, Credential: value}, nil
}

func withMemory(token *auth.TokenData, callback func(string) error) error {
	directory, err := os.MkdirTemp("", "wegent-dws-native-")
	if err != nil {
		return denied
	}
	defer os.RemoveAll(directory)
	originalConfig, hadConfig := os.LookupEnv("DWS_CONFIG_DIR")
	if os.Setenv("DWS_CONFIG_DIR", directory) != nil {
		return denied
	}
	defer func() {
		if hadConfig {
			_ = os.Setenv("DWS_CONFIG_DIR", originalConfig)
		} else {
			_ = os.Unsetenv("DWS_CONFIG_DIR")
		}
	}()
	original := edition.Get()
	hooks := *original
	hooks.Name, hooks.IsEmbedded, hooks.HideAuthLogin = "wegent", true, true
	hooks.AutoPurgeToken = false
	hooks.ConfigDir = func() string { return directory }
	hooks.LoadToken = func(string) ([]byte, error) { return json.Marshal(token) }
	hooks.SaveToken = func(string, []byte) error { return denied }
	hooks.DeleteToken = func(string) error { return denied }
	hooks.OnAuthError = func(string, error) error { return denied }
	hooks.TokenProvider = func(context.Context, func() (string, error)) (string, error) {
		if !token.ExpiresAt.After(time.Now().Add(30 * time.Second)) {
			return "", denied
		}
		return token.AccessToken, nil
	}
	edition.Override(&hooks)
	defer edition.Override(original)
	return callback(directory)
}

func run(ctx context.Context, credential pluginauth.Credential, arguments []string) error {
	if !allowed(arguments) {
		return denied
	}
	token, err := decode(credential)
	if err != nil {
		return err
	}
	if !token.ExpiresAt.After(time.Now().Add(30 * time.Second)) {
		// A controlled business result leaves the host connection available for
		// the next source-sync update. No network call or refresh is attempted.
		return json.NewEncoder(os.Stdout).Encode(map[string]any{
			"success": false, "authenticated": false,
			"error":   "dingtalk_cloud_token_expired",
			"message": "云端认证已过期，请打开本地 WeWork，等待 DWS 刷新同步；本地使用不受影响。",
		})
	}
	// Execute the upstream CLI with the same business command implementation.
	if len(arguments) == 1 && arguments[0] == "account-status" {
		return json.NewEncoder(os.Stdout).Encode(map[string]any{"authenticated": true, "accountId": token.CorpID + ":" + token.UserID})
	}
	// Its private auth store hooks never read/write the platform Keychain.
	return withMemory(token, func(string) error {
		output, err := capture(func() int {
			original := os.Args
			os.Args = append([]string{"dws"}, arguments...)
			defer func() { os.Args = original }()
			return app.Execute()
		})
		if err != nil || bytes.Contains(output, []byte(token.AccessToken)) {
			return denied
		}
		_, err = os.Stdout.Write(output)
		return err
	})
}

func capture(callback func() int) ([]byte, error) {
	reader, writer, err := os.Pipe()
	if err != nil {
		return nil, denied
	}
	defer reader.Close()
	original := os.Stdout
	os.Stdout = writer
	defer func() { os.Stdout = original; writer.Close() }()
	result := make(chan []byte, 1)
	go func() { data, _ := io.ReadAll(io.LimitReader(reader, 1024*1024+1)); result <- data; reader.Close() }()
	code := callback()
	writer.Close()
	output := <-result
	if code != 0 || len(output) > 1024*1024 {
		return nil, denied
	}
	return output, nil
}
