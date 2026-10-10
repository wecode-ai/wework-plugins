// SPDX-License-Identifier: Apache-2.0
package auth

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"
)

type wegentSnapshotTransport func(*http.Request) (*http.Response, error)

func (f wegentSnapshotTransport) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func mockWegentSnapshotHTTP(t *testing.T, f wegentSnapshotTransport) {
	t.Helper()
	old := oauthHTTPClient
	oauthHTTPClient = &http.Client{Transport: f}
	t.Cleanup(func() { oauthHTTPClient = old })
}

func TestWegentSnapshotKeepsValidLocalGrantAndNeverDetaches(t *testing.T) {
	directory, token := wegentTransferFixture(t)
	t.Setenv("DWS_CONFIG_DIR", directory)
	mockWegentSnapshotHTTP(t, func(*http.Request) (*http.Response, error) {
		t.Fatal("valid snapshot made a network request")
		return nil, errors.New("unexpected network")
	})
	for i := 0; i < 2; i++ {
		got, err := SnapshotWegentToken(context.Background(), directory)
		if err != nil || got.RefreshToken != token.RefreshToken {
			t.Fatal("snapshot changed grant")
		}
	}
	got, err := LoadTokenData(directory)
	if err != nil || got.AccessToken != token.AccessToken || got.PersistentCode != token.PersistentCode {
		t.Fatal("local grant was detached")
	}
	if _, err := os.Stat(filepath.Join(directory, "wegent-transfers")); !os.IsNotExist(err) {
		t.Fatal("snapshot created a handoff receipt")
	}
}

func TestWegentSnapshotRefreshPersistsLocallyAndConcurrentExportsReuseIt(t *testing.T) {
	directory, token := wegentTransferFixture(t)
	t.Setenv("DWS_CONFIG_DIR", directory)
	token.ExpiresAt = time.Now().Add(-time.Minute)
	if err := SaveTokenData(directory, token); err != nil {
		t.Fatal(err)
	}
	calls := 0
	mockWegentSnapshotHTTP(t, func(request *http.Request) (*http.Response, error) {
		calls++
		var body map[string]any
		if request.URL.Path != "/oauth2/refreshToken" || json.NewDecoder(request.Body).Decode(&body) != nil ||
			body["refreshToken"] != "synthetic-refresh" || body["clientId"] != "synthetic-client" {
			t.Error("wrong refresh request")
		}
		return &http.Response{StatusCode: 200, Header: make(http.Header), Body: io.NopCloser(strings.NewReader(
			`{"accessToken":"synthetic-new-access","refreshToken":"synthetic-rotated","expiresIn":3600,"corpId":"synthetic-corp","userId":"alice"}`))}, nil
	})
	var wait sync.WaitGroup
	for i := 0; i < 4; i++ {
		wait.Add(1)
		go func() {
			defer wait.Done()
			got, err := SnapshotWegentToken(context.Background(), directory)
			if err != nil || got.AccessToken != "synthetic-new-access" {
				t.Error("snapshot refresh failed")
			}
		}()
	}
	wait.Wait()
	if calls != 1 {
		t.Fatalf("concurrent refresh happened %d times", calls)
	}
	// No backend is involved: upload failure cannot roll back the local rotation.
	got, err := LoadTokenData(directory)
	if err != nil || got.RefreshToken != "synthetic-rotated" || got.PersistentCode != token.PersistentCode {
		t.Fatal("rotated refresh grant was not preserved locally")
	}
}

func TestWegentSnapshotRefreshFailureNeverChangesCredentialOrProfile(t *testing.T) {
	for _, status := range []int{400, 401, 429, 503} {
		t.Run(http.StatusText(status), func(t *testing.T) {
			directory, token := wegentTransferFixture(t)
			t.Setenv("DWS_CONFIG_DIR", directory)
			token.ExpiresAt = time.Now().Add(-time.Minute)
			if err := SaveTokenData(directory, token); err != nil {
				t.Fatal(err)
			}
			before, err := LoadTokenData(directory)
			if err != nil {
				t.Fatal(err)
			}
			profiles, err := LoadProfiles(directory)
			if err != nil {
				t.Fatal(err)
			}
			mockWegentSnapshotHTTP(t, func(*http.Request) (*http.Response, error) {
				return &http.Response{StatusCode: status, Header: make(http.Header),
					Body: io.NopCloser(strings.NewReader(`{"error":"synthetic-secret-must-not-escape"}`))}, nil
			})
			if _, err := SnapshotWegentToken(context.Background(), directory); err != errWegentSnapshot {
				t.Fatal("refresh failure escaped fixed error boundary")
			}
			after, err := LoadTokenData(directory)
			if err != nil || !reflect.DeepEqual(before, after) {
				t.Fatal("failed export changed grant")
			}
			nextProfiles, err := LoadProfiles(directory)
			if err != nil || !reflect.DeepEqual(profiles, nextProfiles) {
				t.Fatal("failed export changed profile status")
			}
		})
	}
}
