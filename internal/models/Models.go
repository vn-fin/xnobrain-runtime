// <Summary>
// Package models aliases the public Studio contracts for internal compatibility.
// New shared data contracts belong in pkg/studio/contract, not this package.
// </Summary>
package models

import "github.com/xno/open-lumora/pkg/studio/contract"

type Agent = contract.Agent
type AgentConfig = contract.AgentConfig
type ProfileConfig = contract.ProfileConfig
type ProfileMetadata = contract.ProfileMetadata
type ProviderConfig = contract.ProviderConfig
type Skill = contract.Skill
type Snapshot = contract.Snapshot
type Conversation = contract.Conversation
type Message = contract.Message
type CronJob = contract.CronJob
type UsageCounter = contract.UsageCounter
type Notification = contract.Notification
type MissedCronSummary = contract.MissedCronSummary
type NotificationResolve = contract.NotificationResolve
type Team = contract.Team
type TeamMember = contract.TeamMember
