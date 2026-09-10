// SPDX-License-Identifier: MIT
package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"runtime"

	"github.com/larksuite/cli/internal/auth"
	"github.com/larksuite/cli/internal/keychain"
	pluginauth "github.com/larksuite/cli/internal/wegentpluginauth"
)

func fingerprint(value string) string {
	sum := sha256.Sum256([]byte(value))
	return hex.EncodeToString(sum[:])
}
func stateRoot() (string, error) {
	root := os.Getenv("WEGENT_LARK_STATE_DIR")
	if !filepath.IsAbs(root) {
		return "", denied
	}
	if err := os.MkdirAll(root, 0700); err != nil {
		return "", denied
	}
	return root, nil
}
func atomicJSON(path string, value any) error {
	data, err := json.Marshal(value)
	if err != nil {
		return denied
	}
	file, err := os.CreateTemp(filepath.Dir(path), ".receipt-")
	if err != nil {
		return denied
	}
	defer os.Remove(file.Name())
	if _, err = file.Write(data); err != nil {
		file.Close()
		return denied
	}
	if err = file.Sync(); err != nil {
		file.Close()
		return denied
	}
	if err = file.Close(); err != nil {
		return denied
	}
	if err = os.Rename(file.Name(), path); err != nil {
		return denied
	}
	if runtime.GOOS != "windows" {
		directory, err := os.Open(filepath.Dir(path))
		if err != nil {
			return denied
		}
		defer directory.Close()
		if directory.Sync() != nil {
			return denied
		}
	}
	return nil
}
func readToken(app, user string) (*auth.StoredUAToken, string, error) {
	if !validString(app) || !validString(user) {
		return nil, "", denied
	}
	value, err := keychain.Get(keychain.LarkCliService, app+":"+user)
	if errors.Is(err, keychain.ErrNotFound) || (err == nil && value == "") {
		return nil, "", nil
	}
	if err != nil || len(value) > 65536 {
		return nil, "", denied
	}
	token := &auth.StoredUAToken{}
	if json.Unmarshal([]byte(value), token) != nil {
		return nil, "", denied
	}
	return token, value, nil
}
func writeManaged(account string) error {
	root, err := stateRoot()
	if err != nil {
		return err
	}
	return atomicJSON(filepath.Join(root, "account-"+fingerprint(account)+".json"), map[string]string{"account_id": account})
}

type receipt struct {
	State       string `json:"state"`
	Fingerprint string `json:"fingerprint"`
}

func detach(ctx context.Context, id string, c pluginauth.Credential) error {
	if validate(c, false) != nil {
		return denied
	}
	p, err := private(c)
	if err != nil {
		return err
	}
	expected, _ := p["source_fingerprint"].(string)
	if len(expected) != 64 {
		return denied
	}
	root, err := stateRoot()
	if err != nil {
		return err
	}
	path := filepath.Join(root, "transfer-"+id+".json")
	previous := receipt{}
	if data, err := os.ReadFile(path); err == nil {
		if json.Unmarshal(data, &previous) != nil || previous.Fingerprint != expected {
			return denied
		}
		if previous.State == "aborted" {
			return pluginauth.ErrSourceChanged
		}
		if previous.State == "detached" {
			return nil
		}
		if previous.State != "pending" {
			return denied
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return denied
	}
	token, raw, err := readToken(text(c, "app_id"), text(c, "open_id"))
	if err != nil {
		return err
	}
	if fingerprint(raw) != expected && !(previous.State == "pending" && token == nil) {
		if atomicJSON(path, receipt{"aborted", expected}) != nil {
			return denied
		}
		return pluginauth.ErrSourceChanged
	}
	if atomicJSON(path, receipt{"pending", expected}) != nil {
		return denied
	}
	// Only remove the exact selected UAT. App credentials and other users remain.
	// Unlike upstream auth logout, this must never revoke the exported grant.
	if token != nil {
		if keychain.Remove(keychain.LarkCliService, text(c, "app_id")+":"+text(c, "open_id")) != nil {
			return denied
		}
		if next, _, err := readToken(text(c, "app_id"), text(c, "open_id")); err != nil || next != nil {
			return denied
		}
	}
	if writeManaged(accountID(c, false)) != nil {
		return denied
	}
	return atomicJSON(path, receipt{"detached", expected})
}
