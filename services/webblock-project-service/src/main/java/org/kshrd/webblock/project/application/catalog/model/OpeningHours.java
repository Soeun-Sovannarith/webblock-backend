package org.kshrd.webblock.project.application.catalog.model;

import java.time.DayOfWeek;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Objects;

/**
 * Pure rules on opening hours: the wizard's seven weekday rows become the catalog's ranges, and the ranges become the
 * two lines a template home page shows. Ranges never wrap past Sunday and none closes the next day.
 */
public final class OpeningHours {

    private static final String[] SHORT_DAY = {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"};
    private static final String NO_HOURS_TEXT = "closed";

    private OpeningHours() {}

    /**
     * Consecutive open days with the same times are one range; closed days give none. The rows may come in any order.
     */
    public static List<OpeningRange> toRanges(List<OpeningHour> week) {
        List<OpeningHour> open = week.stream()
                .filter(OpeningHour::open)
                .sorted(Comparator.comparing(OpeningHour::day))
                .toList();
        List<OpeningRange> ranges = new ArrayList<>();
        OpeningHour first = null;
        OpeningHour last = null;
        for (OpeningHour day : open) {
            if (last != null && continues(last, day)) {
                last = day;
                continue;
            }
            if (first != null) {
                ranges.add(range(first, last));
            }
            first = day;
            last = day;
        }
        if (first != null) {
            ranges.add(range(first, last));
        }
        return List.copyOf(ranges);
    }

    private static boolean continues(OpeningHour previous, OpeningHour next) {
        return next.day().getValue() == previous.day().getValue() + 1
                && Objects.equals(next.opensAt(), previous.opensAt())
                && Objects.equals(next.closesAt(), previous.closesAt());
    }

    private static OpeningRange range(OpeningHour first, OpeningHour last) {
        return new OpeningRange(name(first.day()), name(last.day()), first.opensAt(), first.closesAt(), false);
    }

    private static String name(DayOfWeek day) {
        return day.name();
    }

    /** The times of the first open day, "9:00 – 18:00", or "Closed" when no range exists. */
    public static String hoursText(List<OpeningRange> ranges) {
        return ranges.isEmpty() ? "Closed" : times(ranges.getFirst());
    }

    /** "Mon – Fri 9:00 – 18:00 · Sat closed · Sun closed": runs of days with the same times, closed days included. */
    public static String detailText(List<OpeningRange> ranges) {
        String[] perDay = new String[7];
        for (OpeningRange range : ranges) {
            int from = DayOfWeek.valueOf(range.fromDay()).ordinal();
            int to = DayOfWeek.valueOf(range.toDay()).ordinal();
            for (int d = from; d <= to; d++) {
                perDay[d] = times(range);
            }
        }
        List<String> groups = new ArrayList<>();
        int start = 0;
        for (int d = 1; d <= 7; d++) {
            if (d == 7 || !text(perDay[d]).equals(text(perDay[start]))) {
                String days = start == d - 1 ? SHORT_DAY[start] : SHORT_DAY[start] + " – " + SHORT_DAY[d - 1];
                groups.add(days + " " + text(perDay[start]));
                start = d;
            }
        }
        return String.join(" · ", groups);
    }

    private static String text(String times) {
        return times == null ? NO_HOURS_TEXT : times;
    }

    private static String times(OpeningRange range) {
        return clock(range.opensAt()) + " – " + clock(range.closesAt());
    }

    /** "09:00" becomes "9:00", the way the template sites write times. */
    private static String clock(String hhmm) {
        return hhmm.startsWith("0") ? hhmm.substring(1) : hhmm;
    }
}
