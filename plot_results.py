"""
Plotting Script for Supplement Trial Analysis (Milestone 1.4).
Reads exclusively from Milestone 1.3 analysis summary tables in the database.
"""

import os
import sqlite3
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from matplotlib.ticker import MaxNLocator

DB_PATH = "pubmed_supplements.db"
OUTPUT_DIR = "charts"


def save_chart(filename):
    path = os.path.join(OUTPUT_DIR, filename)
    plt.tight_layout()
    plt.savefig(path, dpi=300)
    plt.close()
    print(f"[Saved] -> {path}")


def plot_question_1(con):
    """Q1: When is the best time to take supplements? (Reads from analysis table)"""
    df = pd.read_sql("SELECT * FROM analysis_q1_timing_summary", con)
    if df.empty:
        print("No data found in analysis_q1_timing_summary.")
        return

    plt.figure(figsize=(7, 6))
    ax = sns.barplot(
        data=df,
        x="timing_window",
        y="count",
        hue="timing_window",
        palette=["#F4B942", "#4B6CB7"],
        legend=False,
    )
    for container in ax.containers:
        ax.bar_label(container, fontsize=12, fontweight="bold")
    plt.title(
        "Question 1: When is the best time to take supplements?",
        fontsize=13,
        fontweight="bold",
        pad=15,
    )
    plt.xlabel("Time of Day", fontsize=11, labelpad=10)
    plt.ylabel("Number of Trials", fontsize=11)
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    save_chart("question_1_morning_vs_evening.png")


def plot_question_2(con):
    """Q2: Is there a cap? (Reads from analysis table)"""
    df = pd.read_sql("SELECT * FROM analysis_q2_limits_summary", con)
    if df.empty:
        print("No data found in analysis_q2_limits_summary.")
        return

    plt.figure(figsize=(7, 6))
    ax = sns.barplot(
        data=df,
        x="has_limit_or_interaction",
        y="count",
        hue="has_limit_or_interaction",
        palette=["#2E8B57", "#9AA5B1"],
        legend=False,
    )
    for container in ax.containers:
        ax.bar_label(container, fontsize=12, fontweight="bold")
    plt.title(
        "Question 2: Is there a cap? Can I take 20 supplements at once and absorb them all?",
        fontsize=12,
        fontweight="bold",
        pad=15,
    )
    plt.xlabel(
        "Trial states an absorption limit or interaction", fontsize=11, labelpad=10
    )
    plt.ylabel("Number of Trials", fontsize=11)
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    save_chart("question_2_absorption_limits.png")


def plot_question_3(con):
    """Q3: Does morning mean before or after a meal for best absorption? (Reads from analysis table)"""
    df = pd.read_sql("SELECT * FROM analysis_q3_fed_fasted_summary", con)
    if df.empty:
        print("No data found in analysis_q3_fed_fasted_summary.")
        return

    plt.figure(figsize=(7, 6))
    ax = sns.barplot(
        data=df,
        x="administration_state",
        y="count",
        hue="administration_state",
        palette=["#E07A5F", "#3D85C6"],
        legend=False,
    )
    for container in ax.containers:
        ax.bar_label(container, fontsize=12, fontweight="bold")
    plt.title(
        "Question 3: Does morning mean before or after a meal for best absorption?",
        fontsize=12,
        fontweight="bold",
        pad=15,
    )
    plt.xlabel(
        "Administration State (Fed = with or after a meal, Fasted = before a meal or empty stomach)",
        fontsize=10,
        labelpad=10,
    )
    plt.ylabel("Number of Trials", fontsize=11)
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    save_chart("question_3_fed_vs_fasted.png")


def milestone1_4():
    if not os.path.exists(DB_PATH):
        print(f"Error: Database '{DB_PATH}' not found. Run Milestone 1.3 first.")
        return

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    con = sqlite3.connect(DB_PATH)

    sns.set_theme(style="whitegrid")
    plot_question_1(con)
    plot_question_2(con)
    plot_question_3(con)
    con.close()
    print(
        f"\nCharts successfully generated from analysis tables and saved in '{OUTPUT_DIR}/'!"
    )


if __name__ == "__main__":
    milestone1_4()
