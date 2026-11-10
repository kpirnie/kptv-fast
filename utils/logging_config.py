"""
Global logging configuration for Unified Streaming Aggregator
"""

import logging
import os

def setup_logging():
    """Setup global logging configuration based on DEBUG environment variable"""
    DEBUG_MODE = os.getenv('DEBUG', 'false').lower() == 'true'
    
    if DEBUG_MODE:
        log_level = logging.DEBUG
        log_format = '%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s'
    else:
        log_level = logging.INFO
        log_format = '%(asctime)s - %(levelname)s - %(message)s'
    
    # Configure root logger
    logging.basicConfig(
        level=log_level,
        format=log_format,
        datefmt='%Y-%m-%d %H:%M:%S',
        force=True  # Override any existing configuration
    )
    
    # Set third-party loggers to WARNING to reduce noise in production
    if not DEBUG_MODE:
        logging.getLogger('urllib3').setLevel(logging.WARNING)
        logging.getLogger('requests').setLevel(logging.WARNING)
        logging.getLogger('gevent').setLevel(logging.WARNING)
    
    return DEBUG_MODE

def get_logger(name: str = None):
    """Get a logger instance with the proper name"""
    if name:
        return logging.getLogger(name)
    return logging.getLogger(__name__)