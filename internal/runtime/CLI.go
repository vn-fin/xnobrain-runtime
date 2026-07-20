// <Summary>
// Package runtime executes one agent turn inside an isolated profile.
// File: CLI.go
// Functions:
//   - NewCLI(binary string, timeout time.Duration) *CLI
//   - (*CLI).Run(ctx context.Context, request Request, emit func(Event)) (Result, error)
//
// </Summary>
package runtime

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"strings"
	"time"
)

type CLI struct {
	binary      string
	timeout     time.Duration
	routerToken string
}

func NewCLI(binary string, timeout time.Duration, routerToken string) *CLI {
	return &CLI{binary: binary, timeout: timeout, routerToken: routerToken}
}

func (c *CLI) Run(ctx context.Context, request Request, emit func(Event)) (Result, error) {
	runCtx, cancel := context.WithTimeout(ctx, c.timeout)
	defer cancel()
	args := []string{"chat", "-q", request.Input, "-Q", "--source", "open-lumora", "--accept-hooks"}
	if request.RuntimeSessionID != "" {
		args = append(args, "--resume", request.RuntimeSessionID)
	}
	if request.Model != "" && request.Model != "auto" {
		args = append(args, "--model", request.Model)
	}
	if len(request.Toolsets) > 0 {
		args = append(args, "--toolsets", strings.Join(request.Toolsets, ","))
	}
	command := exec.CommandContext(runCtx, c.binary, args...)
	command.Dir = request.WorkspacePath
	command.Env = profileEnvironment(request.ProfilePath, c.routerToken)
	stdout, err := command.StdoutPipe()
	if err != nil {
		return Result{}, err
	}
	stderr, err := command.StderrPipe()
	if err != nil {
		return Result{}, err
	}
	if err := command.Start(); err != nil {
		if errors.Is(err, exec.ErrNotFound) {
			return Result{}, fmt.Errorf("runtime binary %q is not installed", c.binary)
		}
		return Result{}, err
	}
	var output strings.Builder
	var runtimeSessionID string
	scanner := bufio.NewScanner(stdout)
	buffer := make([]byte, 64*1024)
	scanner.Buffer(buffer, 4*1024*1024)
	for scanner.Scan() {
		line := scanner.Text()
		if sessionID := parseSessionID(line); sessionID != "" {
			runtimeSessionID = sessionID
			continue
		}
		if output.Len() > 0 {
			output.WriteByte('\n')
		}
		output.WriteString(line)
		emit(Event{Type: "response.output_text.delta", Text: line + "\n", RunID: request.RunID})
	}
	stderrPayload, _ := ioReadAllLimit(stderr, 256*1024)
	waitErr := command.Wait()
	if scanErr := scanner.Err(); scanErr != nil {
		return Result{}, scanErr
	}
	if waitErr != nil {
		message := strings.TrimSpace(string(stderrPayload))
		if message == "" {
			message = waitErr.Error()
		}
		return Result{}, fmt.Errorf("runtime failed: %s", message)
	}
	return Result{Output: strings.TrimSpace(output.String()), RuntimeSessionID: runtimeSessionID}, nil
}

func (c *CLI) ResolveApproval(_ context.Context, _ string, _ string, _ bool) error {
	return fmt.Errorf("the local CLI runtime did not report a pending approval")
}

func profileEnvironment(profilePath string, routerToken string) []string {
	home := profilePath + string(os.PathSeparator) + "home"
	environment := os.Environ()
	environment = replaceEnvironment(environment, "HERMES_HOME", profilePath)
	environment = replaceEnvironment(environment, "HOME", home)
	environment = replaceEnvironment(environment, "HERMES_ACCEPT_HOOKS", "1")
	if routerToken != "" {
		environment = replaceEnvironment(environment, "NINE_ROUTER_API_KEY", routerToken)
	}
	return environment
}

func replaceEnvironment(environment []string, key string, value string) []string {
	prefix := key + "="
	result := make([]string, 0, len(environment)+1)
	for _, item := range environment {
		if !strings.HasPrefix(item, prefix) {
			result = append(result, item)
		}
	}
	return append(result, prefix+value)
}

func parseSessionID(line string) string {
	for _, prefix := range []string{"Session ID:", "session_id:"} {
		if strings.HasPrefix(strings.TrimSpace(line), prefix) {
			return strings.TrimSpace(strings.TrimPrefix(strings.TrimSpace(line), prefix))
		}
	}
	return ""
}

func ioReadAllLimit(file io.Reader, limit int64) ([]byte, error) {
	result := make([]byte, 0)
	buffer := make([]byte, 32*1024)
	for int64(len(result)) < limit {
		count, err := file.Read(buffer)
		if count > 0 {
			remaining := int(limit - int64(len(result)))
			if count > remaining {
				count = remaining
			}
			result = append(result, buffer[:count]...)
		}
		if err != nil {
			if errors.Is(err, os.ErrClosed) || errors.Is(err, context.Canceled) {
				return result, nil
			}
			return result, nil
		}
	}
	return result, nil
}
