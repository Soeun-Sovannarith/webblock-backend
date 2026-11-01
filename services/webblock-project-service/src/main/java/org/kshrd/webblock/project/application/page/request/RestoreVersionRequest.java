package org.kshrd.webblock.project.application.page.request;

import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.PositiveOrZero;

public record RestoreVersionRequest(@NotNull @PositiveOrZero Long expectedEditRevision) {}
