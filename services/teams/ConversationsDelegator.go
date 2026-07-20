// <Summary>
// ConversationsDelegator adapts team work to the existing conversation/Hermes
// runtime and forwards the validated toolset allowlist into the runtime request.
// </Summary>
package teams

import (
	"context"
	"fmt"
	"strings"

	runtimeadapter "github.com/xno/open-lumora/internal/runtime"
	"github.com/xno/open-lumora/services/conversations"
)

type ConversationsDelegator struct{ conversations *conversations.Conversations }

func NewConversationsDelegator(service *conversations.Conversations) *ConversationsDelegator {
	return &ConversationsDelegator{conversations: service}
}

func (d *ConversationsDelegator) Delegate(ctx context.Context, userID string, agentID string, task string, policy DelegationPolicy) (string, error) {
	conversation, err := d.conversations.Create(ctx, userID, agentID, "Delegated "+policy.Role)
	if err != nil {
		return "", err
	}
	prompt := delegatedPrompt(task, policy)
	var final string
	err = d.conversations.StreamDelegated(ctx, userID, agentID, conversation.ID, prompt, policy.Toolsets, func(event runtimeadapter.Event) {
		if event.Type == "run.completed" {
			final = event.Text
		}
	})
	return strings.TrimSpace(final), err
}

func delegatedPrompt(task string, policy DelegationPolicy) string {
	return fmt.Sprintf("You are a delegated %s worker. Complete only the assigned task. Do not ask the parent or user clarifying questions. Do not write memory or skills. Return one final concise summary only; do not delegate further.\n\nAssigned task:\n%s", policy.Role, task)
}
