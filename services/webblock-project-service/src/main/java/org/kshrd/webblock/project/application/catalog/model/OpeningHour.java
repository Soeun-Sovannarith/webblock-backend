package org.kshrd.webblock.project.application.catalog.model;

import java.time.DayOfWeek;

/** One weekday of the wizard's opening hours; the times are "HH:mm" and only read when {@code open}. */
public record OpeningHour(DayOfWeek day, boolean open, String opensAt, String closesAt) {}
