"""
Circuit breaker implementation for database resilience.

Provides fault tolerance by preventing cascading failures when
the database is experiencing issues.
"""

import asyncio
import time
from enum import Enum
from typing import Dict, Any, Callable, Optional, Type
from dataclasses import dataclass
import shared.bblogger as bblogger

flogger = bblogger.get_logger("circuit-breaker")

class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 5
    recovery_timeout: int = 60
    expected_exception: Type[Exception] = Exception
    success_threshold: int = 3  # Successful calls needed to close circuit

class CircuitBreaker:
    """
    Circuit breaker implementation for database operations.
    
    Provides automatic fault tolerance by:
    - Opening circuit after failure threshold is reached
    - Allowing limited testing after recovery timeout
    - Closing circuit after successful operations
    """

    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 60, 
                 expected_exception: Type[Exception] = Exception):
        self.config = CircuitBreakerConfig(
            failure_threshold=failure_threshold,
            recovery_timeout=recovery_timeout,
            expected_exception=expected_exception
        )
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = None
        self._lock = asyncio.Lock()

    async def call(self, func: Callable, *args, **kwargs) -> Any:
        """
        Execute a function with circuit breaker protection.
        
        Args:
            func: Function to execute
            *args, **kwargs: Function arguments
            
        Returns:
            Function result
            
        Raises:
            CircuitBreakerOpenException: When circuit is open
            Original exception: When function fails
        """
        async with self._lock:
            if self.state == CircuitState.OPEN:
                if self._should_attempt_reset():
                    self.state = CircuitState.HALF_OPEN
                    flogger.info("Circuit breaker entering HALF_OPEN state")
                else:
                    raise CircuitBreakerOpenException("Circuit breaker is OPEN")

        try:
            result = await func(*args, **kwargs) if asyncio.iscoroutinefunction(func) else func(*args, **kwargs)
            await self._on_success()
            return result
            
        except self.config.expected_exception as e:
            await self._on_failure()
            raise
        except Exception as e:
            # Unexpected exceptions don't count as failures
            flogger.warning(f"Unexpected exception in circuit breaker: {e}")
            raise

    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to attempt reset."""
        if self.last_failure_time is None:
            return True
        return time.time() - self.last_failure_time >= self.config.recovery_timeout

    async def _on_success(self) -> None:
        """Handle successful operation."""
        async with self._lock:
            self.failure_count = 0
            
            if self.state == CircuitState.HALF_OPEN:
                self.success_count += 1
                if self.success_count >= self.config.success_threshold:
                    self.state = CircuitState.CLOSED
                    self.success_count = 0
                    flogger.info("Circuit breaker CLOSED after successful operations")

    async def _on_failure(self) -> None:
        """Handle failed operation."""
        async with self._lock:
            self.failure_count += 1
            self.last_failure_time = time.time()
            
            if self.state == CircuitState.HALF_OPEN:
                self.state = CircuitState.OPEN
                flogger.warning("Circuit breaker OPEN after failure in HALF_OPEN state")
            elif self.failure_count >= self.config.failure_threshold:
                self.state = CircuitState.OPEN
                flogger.warning(f"Circuit breaker OPEN after {self.failure_count} failures")

    def get_state(self) -> Dict[str, Any]:
        """Get current circuit breaker state information."""
        return {
            "state": self.state.value,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "last_failure_time": self.last_failure_time,
            "failure_threshold": self.config.failure_threshold,
            "recovery_timeout": self.config.recovery_timeout
        }

class CircuitBreakerOpenException(Exception):
    """Exception raised when circuit breaker is open."""
    pass
