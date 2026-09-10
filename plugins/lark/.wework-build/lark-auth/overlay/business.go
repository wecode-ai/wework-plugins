// SPDX-License-Identifier: MIT
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"

	"github.com/larksuite/cli/cmd"
	ext "github.com/larksuite/cli/extension/credential"
	"github.com/larksuite/cli/internal/cmdutil"
	"github.com/larksuite/cli/internal/core"
	"github.com/larksuite/cli/internal/keychain"
	pluginauth "github.com/larksuite/cli/internal/wegentpluginauth"
)

var products = map[string]bool{"approval": true, "attendance": true, "base": true, "calendar": true, "contact": true, "docs": true, "doc": true, "drive": true, "event": true, "im": true, "mail": true, "md": true, "markdown": true, "mindnotes": true, "note": true, "minutes": true, "okr": true, "sheets": true, "slides": true, "task": true, "vc": true, "whiteboard": true, "wiki": true, "api": true, "schema": true, "whoami": true, "account-status": true}

func allowed(args []string) bool {
	if len(args) == 0 || !products[args[0]] {
		return false
	}
	if args[0] == "event" && (len(args) < 2 || (args[1] != "list" && args[1] != "schema")) {
		return false
	}
	for _, arg := range args {
		name := strings.SplitN(arg, "=", 2)[0]
		switch name {
		case "--profile", "--workspace", "--app-id", "--app-secret", "--token", "--access-token", "--base-url", "--brand", "--debug", "--verbose", "--trace", "--config", "--endpoint", "--plugin":
			return false
		}
	}
	return true
}

type noKeychain struct{}

func (noKeychain) Get(string, string) (string, error) { return "", denied }
func (noKeychain) Set(string, string, string) error   { return denied }
func (noKeychain) Remove(string, string) error        { return denied }

type memoryProvider struct {
	credential pluginauth.Credential
	token      string
	bot        bool
}

func (p *memoryProvider) Name() string  { return "wegent" }
func (p *memoryProvider) Priority() int { return -1000 }
func (p *memoryProvider) ResolveAccount(context.Context) (*ext.Account, error) {
	identity, support := ext.IdentityUser, ext.SupportsUser
	if p.bot {
		identity, support = ext.IdentityBot, ext.SupportsBot
	}
	return &ext.Account{AppID: text(p.credential, "app_id"), Brand: ext.Brand(text(p.credential, "brand")), OpenID: text(p.credential, "open_id"), DefaultAs: identity, SupportedIdentities: support}, nil
}
func (p *memoryProvider) ResolveToken(_ context.Context, spec ext.TokenSpec) (*ext.Token, error) {
	expected := ext.TokenTypeUAT
	if p.bot {
		expected = ext.TokenTypeTAT
	}
	if spec.Type != expected || (spec.AppID != "" && spec.AppID != text(p.credential, "app_id")) {
		return nil, &ext.BlockError{Provider: "wegent", Reason: "identity not granted"}
	}
	return &ext.Token{Value: p.token, Scopes: text(p.credential, "scope"), Source: "wegent"}, nil
}

func credentialOrigin(openHost string, req *http.Request) (*url.URL, error) {
	// Upstream downloads may use CDN hosts, but no credential may follow them.
	if req.Header.Get("Authorization") != "" && (req.URL.Scheme != "https" || req.URL.Host != openHost || (req.Host != "" && req.Host != openHost)) {
		return nil, denied
	}
	return nil, nil
}

type limitedBuffer struct{ bytes.Buffer }

var newBusinessTransport = func(host string) *http.Transport {
	return &http.Transport{Proxy: func(req *http.Request) (*url.URL, error) { return credentialOrigin(host, req) }}
}

func (b *limitedBuffer) Write(p []byte) (int, error) {
	if b.Len()+len(p) > 8*1024*1024 {
		return 0, denied
	}
	return b.Buffer.Write(p)
}
func (b *limitedBuffer) WriteString(value string) (int, error) { return b.Write([]byte(value)) }
func runUser(ctx context.Context, c pluginauth.Credential, args []string) error {
	if validate(c, false) != nil || c["refresh_token"] != nil || c["provider_private"] != nil {
		return denied
	}
	return runCLI(ctx, c, args, text(c, "access_token"), false)
}
func runBot(ctx context.Context, c pluginauth.Credential, args []string) error {
	token, err := botToken(ctx, c)
	if err != nil {
		return err
	}
	return runCLI(ctx, c, args, token, true)
}
func runCLI(ctx context.Context, c pluginauth.Credential, args []string, token string, bot bool) error {
	if !allowed(args) {
		return denied
	}
	if len(args) == 1 && args[0] == "account-status" {
		if !bot && userInfo(ctx, c) != nil {
			return denied
		}
		return json.NewEncoder(os.Stdout).Encode(map[string]any{"status": "ok", "accountId": accountID(c, bot)})
	}
	directory, err := os.MkdirTemp("", "wegent-lark-runtime-")
	if err != nil {
		return denied
	}
	defer os.RemoveAll(directory)
	// The upstream command tree gets an empty config location and a denying
	// keychain. User grants are provided only by the in-memory provider.
	os.Setenv("LARKSUITE_CLI_CONFIG_DIR", directory)
	core.SetCurrentWorkspace(core.WorkspaceLocal)
	keychain.WegentAccess = noKeychain{}
	defer func() { keychain.WegentAccess = nil }()
	ext.Register(&memoryProvider{c, token, bot})
	b, _ := brand(c)
	origin, _ := url.Parse(core.ResolveEndpoints(b).Open)
	oldTransport := http.DefaultTransport
	// Keep a concrete Transport so upstream clones preserve this guard.
	http.DefaultTransport = newBusinessTransport(origin.Host)
	defer func() { http.DefaultTransport = oldTransport }()
	var out, errOut limitedBuffer
	root := cmd.Build(ctx, cmdutil.InvocationContext{}, cmd.WithIO(strings.NewReader(""), &out, &errOut), cmd.WithKeychain(noKeychain{}), cmd.WithoutPlugins(), cmd.HideProfile(true))
	root.SetArgs(args)
	if root.ExecuteContext(ctx) != nil {
		return denied
	}
	for _, secret := range []string{token, text(c, "password")} {
		if secret != "" && (bytes.Contains(out.Bytes(), []byte(secret)) || bytes.Contains(errOut.Bytes(), []byte(secret))) {
			return denied
		}
	}
	_, err = io.Copy(os.Stdout, &out)
	return err
}
