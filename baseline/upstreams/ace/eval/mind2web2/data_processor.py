"""
Data processor for Mind2Web web navigation task (50-candidate version).

Task: Given a webpage with ~50 candidate elements and a navigation task
description with action history, select the correct element and specify
the action (CLICK, TYPE, or SELECT with value).

Evaluation: Three-level matching (element index + operation type + value).
"""
import os
import json
import re
from typing import List, Dict, Any


def load_data(data_path: str) -> List[Dict[str, Any]]:
    """
    Load and process data from a JSONL file.

    Args:
        data_path: Path to the JSONL file

    Returns:
        List of dictionaries containing the data
    """
    if not os.path.exists(data_path):
        raise FileNotFoundError(f"Data file not found: {data_path}")

    data = []
    with open(data_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                data.append(json.loads(line))

    print(f"Loaded {len(data)} samples from {data_path}")
    return data


class DataProcessor:
    """
    Processor for Mind2Web web navigation task (50-candidate version).

    Handles element selection + action prediction on web pages.
    Each sample presents ~50 candidate elements and the model must select
    the correct one and specify the action (CLICK/TYPE/SELECT).
    """

    def __init__(self, task_name: str):
        """
        Initialize the data processor.

        Args:
            task_name: The name of the task (e.g., 'mind2web2')
        """
        self.task_name = task_name

    def process_task_data(self, raw_data: List[Dict]) -> List[Dict]:
        """
        Convert raw Mind2Web data into standardized format for ACE.

        Input format (from JSONL, produced by prepare_data.py):
            {
                "context": "Candidate elements on the current webpage:\\n[0] <select> ...\\n...",
                "question": "Task: ... Website: ...\\nActions completed:\\n...\\nSelect...",
                "target": "[7] SELECT [combobox] Reservation type: Pickup",
                "annotation_id": "...",
                "step_idx": 0,
                "total_steps": 11,
                "domain": "Travel",
                "website": "exploretock",
                "action_repr": "[combobox]  Reservation type -> SELECT: Pickup",
                "operation": {"op": "SELECT", "value": "Pickup"},
                "n_candidates": 50,
                "correct_candidate_idx": 7
            }

        Output format (standardized for ACE):
            {
                "context": "<candidate element list>",
                "question": "<task + history + instruction>",
                "target": "[7] SELECT [combobox] Reservation type: Pickup",
                "others": { ... metadata ... }
            }

        Args:
            raw_data: Raw data loaded from JSONL

        Returns:
            List of dicts in standardized format
        """
        processed_data = []

        for item in raw_data:
            processed_item = {
                "context": item.get("context", ""),
                "question": item.get("question", ""),
                "target": item.get("target", ""),
                "others": {
                    "annotation_id": item.get("annotation_id", ""),
                    "step_idx": item.get("step_idx", 0),
                    "total_steps": item.get("total_steps", 0),
                    "domain": item.get("domain", ""),
                    "website": item.get("website", ""),
                    "action_repr": item.get("action_repr", ""),
                    "operation": item.get("operation", {}),
                    "n_candidates": item.get("n_candidates", 0),
                    "correct_candidate_idx": item.get("correct_candidate_idx", -1),
                    "task": self.task_name
                }
            }
            processed_data.append(processed_item)

        return processed_data

    def _parse_prediction(self, text: str) -> dict:
        """
        Parse a model prediction to extract element index, operation, and value.

        Expected format: "[idx] OP [tag] element_text: value"
        But models may produce variations, so we use flexible parsing.

        Returns:
            dict with keys: 'element_idx', 'op', 'value' (any may be None)
        """
        result = {"element_idx": None, "op": None, "value": None}
        text = text.strip()

        # Try to extract element index: [number]
        idx_match = re.search(r'\[(\d+)\]', text)
        if idx_match:
            result["element_idx"] = int(idx_match.group(1))

        # Try to extract operation type
        for op in ["SELECT", "TYPE", "CLICK"]:
            if op in text.upper():
                result["op"] = op
                break

        # Try to extract value (after the last colon for TYPE/SELECT)
        if result["op"] in ["SELECT", "TYPE"]:
            # Look for ": value" pattern
            value_match = re.search(r':\s*(.+?)$', text)
            if value_match:
                result["value"] = value_match.group(1).strip()

        return result

    def answer_is_correct(self, predicted: str, ground_truth: str) -> bool:
        """
        Check if prediction matches ground truth.

        Checks three components with decreasing strictness:
        1. Element index must match (most important)
        2. Operation type must match
        3. Value must match for TYPE/SELECT (flexible matching)

        Args:
            predicted: Model's answer
            ground_truth: Ground truth answer

        Returns:
            bool: True if answer is correct
        """
        pred = self._parse_prediction(predicted)
        truth = self._parse_prediction(ground_truth)

        # Element index must match
        if pred["element_idx"] is None or pred["element_idx"] != truth["element_idx"]:
            return False

        # Operation type must match
        if pred["op"] is None or pred["op"] != truth["op"]:
            return False

        # For CLICK, element + op match is sufficient
        if truth["op"] == "CLICK":
            return True

        # For TYPE/SELECT, value must also match
        if truth["value"] is None:
            return True  # No value to check

        if pred["value"] is None:
            return False

        # Flexible value comparison (case-insensitive, strip whitespace)
        return pred["value"].strip().lower() == truth["value"].strip().lower()

    def evaluate_accuracy(self, out: List[str], target: List[str]) -> float:
        """
        Calculate accuracy across multiple predictions.

        Also reports sub-metrics:
        - Element selection accuracy
        - Operation type accuracy
        - Full match accuracy (element + op + value)

        Args:
            out: List of model predictions
            target: List of ground truth targets

        Returns:
            Accuracy as float between 0 and 1
        """
        if len(out) != len(target):
            raise ValueError("Predictions and ground truths must have the same length.")

        correct_count = 0
        elem_correct = 0
        op_correct = 0

        for predicted, ground_truth in zip(out, target):
            pred = self._parse_prediction(predicted)
            truth = self._parse_prediction(ground_truth)

            if pred["element_idx"] == truth["element_idx"] and truth["element_idx"] is not None:
                elem_correct += 1
            if pred["op"] == truth["op"] and truth["op"] is not None:
                op_correct += 1
            if self.answer_is_correct(predicted, ground_truth):
                correct_count += 1

        n = len(out) if out else 1
        print(f"  Element selection accuracy: {elem_correct}/{n} = {elem_correct/n:.3f}")
        print(f"  Operation type accuracy: {op_correct}/{n} = {op_correct/n:.3f}")
        print(f"  Full match accuracy: {correct_count}/{n} = {correct_count/n:.3f}")

        accuracy = correct_count / n
        return accuracy
