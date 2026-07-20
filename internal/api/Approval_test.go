// <Summary>
// Approval event tests keep the public choice set small while preserving
// permanent, profile-scoped memory and skill permissions.
// </Summary>
package api

import (
	"reflect"
	"testing"

	runtimeadapter "github.com/xno/open-lumora/internal/runtime"
)

func TestProfileWriteApprovalOffersOnlyOnceAlwaysAndDeny(t *testing.T) {
	payload := eventPayload(runtimeadapter.Event{
		Type:       "approval.request",
		PatternKey: "memory_write",
	})

	if payload["allow_permanent"] != true {
		t.Fatalf("allow_permanent = %#v", payload["allow_permanent"])
	}
	if !reflect.DeepEqual(payload["choices"], []string{"once", "always", "deny"}) {
		t.Fatalf("choices = %#v", payload["choices"])
	}
}

func TestNonPermanentApprovalDoesNotOfferAlways(t *testing.T) {
	payload := eventPayload(runtimeadapter.Event{
		Type:       "approval.request",
		PatternKey: "restricted_operation",
	})

	if payload["allow_permanent"] != false {
		t.Fatalf("allow_permanent = %#v", payload["allow_permanent"])
	}
	if !reflect.DeepEqual(payload["choices"], []string{"once", "deny"}) {
		t.Fatalf("choices = %#v", payload["choices"])
	}
}
