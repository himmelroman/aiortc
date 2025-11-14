"""
Google Congestion Control (GCC) implementation.

This module provides GCC bandwidth estimation for WebRTC,
combining delay-based and loss-based congestion control.
"""

from .estimator import SenderSideBandwidthEstimator  # noqa: F401

__all__ = ["SenderSideBandwidthEstimator"]
