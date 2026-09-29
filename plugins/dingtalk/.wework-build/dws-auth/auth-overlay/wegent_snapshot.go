// SPDX-License-Identifier: Apache-2.0
package auth

import (
	"context"
	"errors"
	"io"

	"github.com/DingTalk-Real-AI/dingtalk-workspace-cli/pkg/config"
)

var errWegentSnapshot = errors.New("plugin_auth_dws_snapshot_unavailable")

// SnapshotWegentToken keeps the local store as the sole refresh owner.
// Unlike GetTokenSnapshot, a background export failure does not mark a profile
// expired. Use the same dual lock, persistence preflight and refresh routine as
// the CLI, but never login, detach, revoke or delete credentials here.
func SnapshotWegentToken(ctx context.Context, directory string) (*TokenData, error) {
	if config.GetMCPBaseURL() != config.DefaultMCPBaseURL {
		return nil, errWegentSnapshot
	}
	lock, err := AcquireDualLock(ctx, directory)
	if err != nil {
		return nil, errWegentSnapshot
	}
	defer lock.Release()
	token, err := loadOAuthTokenUnderHeldLock(directory, RuntimeProfile())
	if err != nil || token == nil || token.CorpID == "" || token.UserID == "" ||
		token.Source != "mcp" || token.ClientID == "" || token.AccessToken == "" {
		return nil, errWegentSnapshot
	}
	if token.IsAccessTokenValid() {
		return token, nil
	}
	if !token.IsRefreshTokenValid() || preflightTokenRefreshPersistence(directory, token) != nil {
		return nil, errWegentSnapshot
	}
	provider := NewOAuthProvider(directory, nil)
	provider.Output = io.Discard
	refreshed, err := provider.refreshWithRefreshToken(ctx, token)
	if err != nil || refreshed == nil || refreshed.CorpID != token.CorpID ||
		refreshed.UserID != token.UserID || !refreshed.IsAccessTokenValid() {
		return nil, errWegentSnapshot
	}
	return refreshed, nil
}
