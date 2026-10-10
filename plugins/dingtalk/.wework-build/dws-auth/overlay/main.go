// SPDX-License-Identifier: Apache-2.0
// Built inside the pinned upstream module; no upstream source patch is required.
package main

import (
	"context"
	"fmt"
	"os"

	pluginauth "github.com/DingTalk-Real-AI/dingtalk-workspace-cli/internal/wegentpluginauth"
)

func main() {
	// Only short-lived snapshots cross the private channel. There is deliberately
	// no detach, refresh or revoke operation for the cloud connector.
	provider := pluginauth.Provider{Export: export, Allowed: allowed, Run: run}
	if err := pluginauth.Serve(context.Background(), "dingtalk-access", "bearer", provider, os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, "plugin_auth_dws_failed")
		os.Exit(1)
	}
}
