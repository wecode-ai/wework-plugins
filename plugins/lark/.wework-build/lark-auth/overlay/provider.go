// SPDX-License-Identifier: MIT
package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/larksuite/cli/internal/auth"
	"github.com/larksuite/cli/internal/core"
	"github.com/larksuite/cli/internal/keychain"
	pluginauth "github.com/larksuite/cli/internal/wegentpluginauth"
)

var denied = errors.New("plugin_auth_lark_denied")
var client = &http.Client{Timeout: 25 * time.Second, Transport: &http.Transport{Proxy: nil}, CheckRedirect: func(*http.Request, []*http.Request) error { return denied }}

func text(c pluginauth.Credential, key string) string { value, _ := c[key].(string); return value }
func brand(c pluginauth.Credential) (core.LarkBrand, error) {
	value := text(c, "brand")
	if value != "feishu" && value != "lark" {
		return "", denied
	}
	return core.LarkBrand(value), nil
}
func validString(value string) bool {
	return value != "" && len(value) < 8192 && !strings.ContainsAny(value, "\r\n\x00")
}
func validate(c pluginauth.Credential, bot bool) error {
	if _, err := brand(c); err != nil {
		return denied
	}
	if !validString(text(c, "app_id")) {
		return denied
	}
	if bot {
		if text(c, "username") != text(c, "app_id") || !validString(text(c, "password")) {
			return denied
		}
	} else if !validString(text(c, "open_id")) || !validString(text(c, "access_token")) {
		return denied
	}
	return nil
}
func accountID(c pluginauth.Credential, bot bool) string {
	id := text(c, "brand") + ":" + text(c, "app_id")
	if !bot {
		id += ":" + text(c, "open_id")
	}
	return id
}
func userInfo(ctx context.Context, c pluginauth.Credential) error {
	b, err := brand(c)
	if err != nil {
		return err
	}
	req, err := http.NewRequestWithContext(ctx, "GET", core.ResolveEndpoints(b).Open+auth.PathUserInfoV1, nil)
	if err != nil {
		return denied
	}
	req.Header.Set("Authorization", "Bearer "+text(c, "access_token"))
	result, err := requestJSON(req)
	if err != nil {
		return err
	}
	data, _ := result["data"].(map[string]any)
	if data["open_id"] != text(c, "open_id") {
		return denied
	}
	return nil
}
func requestJSON(req *http.Request) (map[string]any, error) {
	response, err := client.Do(req)
	if err != nil {
		return nil, denied
	}
	defer response.Body.Close()
	body, err := io.ReadAll(io.LimitReader(response.Body, 65537))
	if err != nil || len(body) > 65536 || response.StatusCode < 200 || response.StatusCode >= 300 {
		return nil, denied
	}
	result := map[string]any{}
	if len(body) > 0 && json.Unmarshal(body, &result) != nil {
		return nil, denied
	}
	if result["error"] != nil && result["error"] != "" {
		return nil, denied
	}
	if code, ok := result["code"].(float64); ok && code != 0 {
		return nil, denied
	}
	return result, nil
}
func oauth(ctx context.Context, endpoint string, fields url.Values) (map[string]any, error) {
	req, err := http.NewRequestWithContext(ctx, "POST", endpoint, strings.NewReader(fields.Encode()))
	if err != nil {
		return nil, denied
	}
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	// The caller owns uncertain rotating-token results. Never replay a refresh.
	return requestJSON(req)
}
func sourceConfig() (*core.CliConfig, error) {
	cfg, err := core.RequireConfig(keychain.Default())
	if err != nil || (cfg.Brand != core.BrandFeishu && cfg.Brand != core.BrandLark) || !validString(cfg.AppID) || !validString(cfg.AppSecret) {
		return nil, denied
	}
	return cfg, nil
}
func exportUser(ctx context.Context) (pluginauth.Account, error) {
	cfg, err := sourceConfig()
	if err != nil {
		return pluginauth.Account{}, err
	}
	token, raw, err := readToken(cfg.AppID, cfg.UserOpenId)
	if err != nil || token == nil || token.AppId != cfg.AppID || token.UserOpenId != cfg.UserOpenId || token.RefreshToken == "" {
		return pluginauth.Account{}, denied
	}
	c := pluginauth.Credential{"brand": string(cfg.Brand), "app_id": cfg.AppID, "open_id": cfg.UserOpenId, "access_token": token.AccessToken, "refresh_token": token.RefreshToken, "expires_at": token.ExpiresAt / 1000, "refresh_expires_at": token.RefreshExpiresAt / 1000, "scope": token.Scope, "provider_private": map[string]any{"app_secret": cfg.AppSecret, "source_fingerprint": fingerprint(raw)}}
	if validate(c, false) != nil || userInfo(ctx, c) != nil {
		return pluginauth.Account{}, denied
	}
	return pluginauth.Account{ID: accountID(c, false), Credential: c}, nil
}
func exportBot(ctx context.Context) (pluginauth.Account, error) {
	cfg, err := sourceConfig()
	if err != nil {
		return pluginauth.Account{}, err
	}
	c := pluginauth.Credential{"brand": string(cfg.Brand), "app_id": cfg.AppID, "username": cfg.AppID, "password": cfg.AppSecret}
	if _, err := botToken(ctx, c); err != nil {
		return pluginauth.Account{}, err
	}
	return pluginauth.Account{ID: accountID(c, true), Credential: c}, nil
}
func botToken(ctx context.Context, c pluginauth.Credential) (string, error) {
	if validate(c, true) != nil {
		return "", denied
	}
	b, _ := brand(c)
	result, err := oauth(ctx, core.ResolveEndpoints(b).Accounts+core.OAuthTokenV3Path, url.Values{"grant_type": {"client_credentials"}, "client_id": {text(c, "app_id")}, "client_secret": {text(c, "password")}})
	if err != nil {
		return "", err
	}
	token, _ := result["access_token"].(string)
	if !validString(token) {
		return "", denied
	}
	return token, nil
}
func private(c pluginauth.Credential) (map[string]any, error) {
	p, ok := c["provider_private"].(map[string]any)
	if !ok {
		return nil, denied
	}
	secret, _ := p["app_secret"].(string)
	if !validString(secret) {
		return nil, denied
	}
	return p, nil
}
func refresh(ctx context.Context, c pluginauth.Credential) (pluginauth.Account, error) {
	if validate(c, false) != nil || !validString(text(c, "refresh_token")) {
		return pluginauth.Account{}, denied
	}
	p, err := private(c)
	if err != nil {
		return pluginauth.Account{}, err
	}
	b, _ := brand(c)
	result, err := oauth(ctx, auth.ResolveOAuthEndpoints(b).Token, url.Values{"grant_type": {"refresh_token"}, "client_id": {text(c, "app_id")}, "client_secret": {p["app_secret"].(string)}, "refresh_token": {text(c, "refresh_token")}})
	if err != nil {
		return pluginauth.Account{}, err
	}
	access, _ := result["access_token"].(string)
	expires, _ := result["expires_in"].(float64)
	if !validString(access) || expires <= 0 || expires > 31536000 {
		return pluginauth.Account{}, denied
	}
	next := pluginauth.Credential{}
	for k, v := range c {
		next[k] = v
	}
	next["access_token"], next["expires_at"] = access, time.Now().Unix()+int64(expires)
	if value, ok := result["refresh_token"].(string); ok && value != "" {
		next["refresh_token"] = value
	}
	if value, ok := result["refresh_token_expires_in"].(float64); ok && value > 0 {
		next["refresh_expires_at"] = time.Now().Unix() + int64(value)
	}
	if value, ok := result["scope"].(string); ok && value != "" {
		next["scope"] = value
	}
	return pluginauth.Account{ID: accountID(next, false), Credential: next}, nil
}
func revoke(ctx context.Context, c pluginauth.Credential) error {
	if validate(c, false) != nil {
		return denied
	}
	p, err := private(c)
	if err != nil {
		return err
	}
	b, _ := brand(c)
	token, hint := text(c, "refresh_token"), "refresh_token"
	if token == "" {
		token, hint = text(c, "access_token"), "access_token"
	}
	_, err = oauth(ctx, auth.ResolveOAuthEndpoints(b).Revoke, url.Values{"client_id": {text(c, "app_id")}, "client_secret": {p["app_secret"].(string)}, "token": {token}, "token_type_hint": {hint}})
	return err
}
func localContext() (map[string]string, error) {
	cfg, err := core.LoadMultiAppConfig()
	if err != nil {
		return nil, denied
	}
	app := cfg.CurrentAppConfig("")
	if app == nil {
		return nil, denied
	}
	if app.Brand != core.BrandFeishu && app.Brand != core.BrandLark {
		return nil, denied
	}
	bot := string(app.Brand) + ":" + app.AppId
	user := ""
	if len(app.Users) > 0 {
		user = bot + ":" + app.Users[0].UserOpenId
	}
	return map[string]string{"user": user, "bot": bot, "default_as": string(app.DefaultAs)}, nil
}
func localHealth() error {
	cfg, err := sourceConfig()
	if err != nil {
		return err
	}
	token, err := auth.GetValidAccessToken(client, auth.NewUATCallOptions(cfg, io.Discard))
	if err != nil || token == "" {
		return denied
	}
	return nil
}
func clearLocal() error {
	cfg, err := core.LoadMultiAppConfig()
	if err != nil {
		return denied
	}
	app := cfg.CurrentAppConfig("")
	if app == nil {
		return denied
	}
	if len(app.Users) > 0 {
		user := app.Users[0]
		if err := keychain.Remove(keychain.LarkCliService, fmt.Sprintf("%s:%s", app.AppId, user.UserOpenId)); err != nil && !errors.Is(err, keychain.ErrNotFound) {
			return denied
		}
		app.Users = app.Users[1:]
	}
	return core.SaveMultiAppConfig(cfg)
}
