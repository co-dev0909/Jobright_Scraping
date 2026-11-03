"""The Package tab lets users save their own profile answers locally."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from profile_store import (CITIZENSHIP_CHOICES, DEFAULT_PROFILE, ProfileStore,
                           ProfileStoreError, VETERAN_CHOICES)


class ProfileTab(ttk.Frame):
    def __init__(self, parent, store: ProfileStore | None = None) -> None:
        super().__init__(parent, padding=22)
        self.store = store if store is not None else ProfileStore()
        self.fields = {name: tk.StringVar(self, value=value)
                       for name, value in DEFAULT_PROFILE.items()}
        self.status = tk.StringVar(self, value="Enter your profile, then click Save Profile.")
        self.columnconfigure(1, weight=1)
        ttk.Label(self, text="Profile information", style="Title.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        ttk.Label(self, text="Saved on this computer. Choose your own answers; unspecified fields stay unspecified.",
                  wraplength=810).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 20))

        field_definitions = (
            ("full_name", "Full Name", None),
            ("linkedin_info", "LinkedIn Info", None),
            ("us_citizen", "US Citizen", CITIZENSHIP_CHOICES),
            ("not_protected_veteran", "Not a protected veteran", VETERAN_CHOICES),
            ("gender", "Gender", ("Not specified", "Female", "Male", "Non-binary", "Prefer not to answer")),
            ("race", "Race", ("Not specified", "American Indian or Alaska Native", "Asian",
                               "Black or African American", "Hispanic or Latino",
                               "Native Hawaiian or Other Pacific Islander", "White",
                               "Two or more races", "Prefer not to answer")),
        )
        self.widgets = {}
        for row, (name, label, choices) in enumerate(field_definitions, start=2):
            ttk.Label(self, text=label).grid(row=row, column=0, sticky="w", padx=(0, 18), pady=8)
            if choices is None:
                widget = ttk.Entry(self, textvariable=self.fields[name])
            else:
                state = "readonly" if name in {"us_citizen", "not_protected_veteran"} else "normal"
                widget = ttk.Combobox(self, textvariable=self.fields[name], values=choices, state=state)
            widget.grid(row=row, column=1, sticky="ew", pady=8)
            self.widgets[name] = widget
        ttk.Label(self, text="LinkedIn Info can be your profile URL or handle. Gender and Race also accept your own text.",
                  wraplength=810).grid(row=8, column=0, columnspan=2, sticky="w", pady=(4, 18))
        self.save_button = ttk.Button(self, text="Save Profile", command=self.save_profile)
        self.save_button.grid(row=9, column=0, columnspan=2, sticky="w")
        ttk.Label(self, textvariable=self.status, wraplength=810).grid(
            row=10, column=0, columnspan=2, sticky="w", pady=(12, 0))
        self._load_profile()

    def _load_profile(self) -> None:
        try:
            values = self.store.load()
        except ProfileStoreError as error:
            self.status.set(str(error))
            return
        for name, value in values.items():
            self.fields[name].set(value)
        self.status.set("Profile loaded. Changes are saved only when you click Save Profile.")

    def save_profile(self) -> None:
        values = {name: variable.get() for name, variable in self.fields.items()}
        self.save_button.configure(state="disabled")
        try:
            self.store.save(values)
        except ProfileStoreError as error:
            self.status.set(str(error))
        else:
            # Reflect the same whitespace normalization as the store without
            # rereading or exposing the stored profile through a status string.
            for name, value in values.items():
                trimmed = value.strip()
                self.fields[name].set(trimmed or ("Not specified" if name in {"gender", "race"} else ""))
            self.status.set("Profile saved on this computer.")
        finally:
            self.save_button.configure(state="normal")
