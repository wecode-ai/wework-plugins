// SPDX-License-Identifier: MIT
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"

	"github.com/larksuite/cli/cmd"
	pluginauth "github.com/larksuite/cli/internal/wegentpluginauth"
)

func main() {
	args := os.Args[1:]
	if len(args) < 1 {
		os.Exit(1)
	}
	switch args[0] {
	case "local":
		os.Args = append([]string{"lark-cli"}, args[1:]...)
		os.Exit(cmd.Execute())
	case "context":
		value, err := localContext()
		if err != nil {
			os.Exit(1)
		}
		_ = json.NewEncoder(os.Stdout).Encode(value)
		return
	case "local-health", "local-clear":
		var err error
		if args[0] == "local-clear" {
			err = clearLocal()
		} else {
			err = localHealth()
		}
		if err != nil {
			os.Exit(1)
		}
		return
	}
	bot := args[0] == "bot"
	if !bot && args[0] != "user" {
		os.Exit(1)
	}
	slug, kind := "lark", "oauth2"
	provider := pluginauth.Provider{Export: exportUser, Detach: detach, Refresh: refresh, Revoke: revoke, Allowed: allowed, Run: runUser}
	if bot {
		slug, kind = "lark-app", "password"
		provider = pluginauth.Provider{Export: exportBot, Allowed: allowed, Run: runBot}
	}
	if err := pluginauth.Serve(context.Background(), slug, kind, provider, args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, "plugin_auth_lark_failed")
		os.Exit(1)
	}
}
