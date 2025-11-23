// SPDX-FileCopyrightText: 2023 The Pion community <https://pion.ly>
// SPDX-License-Identifier: MIT

package gcc

import (
	"log"
	"sort"
	"time"

	"github.com/pion/interceptor/internal/cc"
)

var acksReceived = 0
var groupsFormed = 0

type arrivalGroupAccumulator struct {
	interDepartureThreshold          time.Duration
	interArrivalThreshold            time.Duration
	interGroupDelayVariationTreshold time.Duration
}

func newArrivalGroupAccumulator() *arrivalGroupAccumulator {
	return &arrivalGroupAccumulator{
		interDepartureThreshold:          5 * time.Millisecond,
		interArrivalThreshold:            5 * time.Millisecond,
		interGroupDelayVariationTreshold: 0,
	}
}

func (a *arrivalGroupAccumulator) run(in <-chan []cc.Acknowledgment, agWriter func(arrivalGroup)) {
	init := false
	group := arrivalGroup{}
	droppedOutOfOrder := 0
	droppedDeparture := 0

	for acks := range in {
		// Sort acknowledgments by arrival time to handle TWCC packets with negative deltas
		// (which are valid per spec for out-of-order packet arrivals)
		sort.Slice(acks, func(i, j int) bool {
			return acks[i].Arrival.Before(acks[j].Arrival)
		})

		acksReceived += len(acks)
		if len(acks) > 0 && acksReceived%100 < len(acks) {
			log.Printf("🔧 DEBUG ArrivalGroupAccumulator: received %d acks (total: %d, groups: %d, dropped: order=%d, depart=%d)",
				len(acks), acksReceived, groupsFormed, droppedOutOfOrder, droppedDeparture)
		}

		for _, next := range acks {
			if !init {
				group = newArrivalGroup(next)
				init = true
				log.Printf("🔧 DEBUG ArrivalGroupAccumulator: initialized first group")
				continue
			}
			if next.Arrival.Before(group.arrival) {
				// New batch with earlier timestamps - write current group and start fresh
				// This handles TWCC negative deltas (out-of-order arrivals per spec)
				agWriter(group)
				groupsFormed++
				group = newArrivalGroup(next)
				continue
			}
			if next.Departure.After(group.departure) {
				// A sequence of packets which are sent within a burst_time interval
				// constitute a group.
				if interDepartureTimePkt(group, next) <= a.interDepartureThreshold {
					group.add(next)
					continue
				}

				// A Packet which has an inter-arrival time less than burst_time and
				// an inter-group delay variation d(i) less than 0 is considered
				// being part of the current group of packets.
				if interArrivalTimePkt(group, next) <= a.interArrivalThreshold &&
					interGroupDelayVariationPkt(group, next) < a.interGroupDelayVariationTreshold {
					group.add(next)
					continue
				}

				agWriter(group)
				groupsFormed++
				group = newArrivalGroup(next)
			} else {
				droppedDeparture++
				if droppedDeparture <= 5 {
					log.Printf("🔧 DEBUG Drop: next.Departure=%v, group.departure=%v, next.Arrival=%v, group.arrival=%v",
						next.Departure, group.departure, next.Arrival, group.arrival)
				}
			}
		}
	}
}

func interArrivalTimePkt(group arrivalGroup, ack cc.Acknowledgment) time.Duration {
	return ack.Arrival.Sub(group.arrival)
}

func interDepartureTimePkt(group arrivalGroup, ack cc.Acknowledgment) time.Duration {
	if len(group.packets) == 0 {
		return 0
	}

	return ack.Departure.Sub(group.departure)
}

func interGroupDelayVariationPkt(group arrivalGroup, ack cc.Acknowledgment) time.Duration {
	return ack.Arrival.Sub(group.arrival) - ack.Departure.Sub(group.departure)
}
