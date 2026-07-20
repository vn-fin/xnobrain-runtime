// <Summary>
// RuntimeDispatcher routes validated commands into shared cron services and
// stores compact cloud missed-run notifications through the repository contract.
// File: RuntimeDispatcher.go
// Types: RuntimeDispatcher
// </Summary>
package deviceconnector

import (
	"context"

	"github.com/xno/open-lumora/pkg/studio/contract"
	"github.com/xno/open-lumora/services/crons"
)

type RuntimeDispatcher struct {
	userID        string
	crons         *crons.Crons
	notifications contract.NotificationStore
}

func NewRuntimeDispatcher(userID string, cronService *crons.Crons, notifications contract.NotificationStore) *RuntimeDispatcher {
	return &RuntimeDispatcher{userID: userID, crons: cronService, notifications: notifications}
}

func (d *RuntimeDispatcher) ExecuteManagedCron(ctx context.Context, agentID string, cronID string, _ string) error {
	return d.crons.RunManaged(ctx, d.userID, agentID, cronID)
}

func (d *RuntimeDispatcher) StoreNotifications(ctx context.Context, notifications []contract.Notification) error {
	if d.notifications == nil {
		return nil
	}
	for _, notification := range notifications {
		if _, err := d.notifications.CreateNotification(ctx, d.userID, notification); err != nil {
			return err
		}
	}
	return nil
}
