"""
Logging configuration for SPD Benefits Extraction.
"""

import logging
import sys
from pathlib import Path
from typing import Optional


def setup_logging(
    level: int = logging.INFO,
    log_file: Optional[str] = None,
    format_string: Optional[str] = None,
) -> logging.Logger:
    """
    Configure logging for the application.
    
    Args:
        level: Logging level (default: INFO)
        log_file: Optional path to log file
        format_string: Custom format string
        
    Returns:
        Configured logger
    """
    if format_string is None:
        format_string = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    
    handlers = [logging.StreamHandler(sys.stdout)]
    
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file))
    
    logging.basicConfig(
        level=level,
        format=format_string,
        handlers=handlers,
        force=True,  # Reset existing handlers
    )
    
    # Reduce noise from third-party libraries
    logging.getLogger("azure").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("pdfminer").setLevel(logging.WARNING)
    logging.getLogger("pdfplumber").setLevel(logging.WARNING)
    
    logger = logging.getLogger(__name__)
    return logger


# Legacy function name for compatibility
def configure_logging():
    """Legacy function for backward compatibility."""
    return setup_logging()


# Initialize logger on import
logger = setup_logging()