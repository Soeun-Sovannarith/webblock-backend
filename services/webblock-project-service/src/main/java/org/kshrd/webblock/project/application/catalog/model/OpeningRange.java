package org.kshrd.webblock.project.application.catalog.model;

import com.fasterxml.jackson.annotation.JsonProperty;

/**
 * One range of catalog.business_profiles.opening_hours (Notion "6 · Shared catalog"): the days from {@code fromDay} to
 * {@code toDay} (java.time.DayOfWeek names such as MONDAY, Monday first, no wrap past Sunday) open from {@code opensAt} to
 * {@code closesAt} ("HH:mm"). The JSON names are the catalog's.
 */
public record OpeningRange(
        @JsonProperty("from_day") String fromDay,
        @JsonProperty("to_day") String toDay,
        @JsonProperty("opens_at") String opensAt,
        @JsonProperty("closes_at") String closesAt,
        @JsonProperty("closes_next_day") boolean closesNextDay) {}
