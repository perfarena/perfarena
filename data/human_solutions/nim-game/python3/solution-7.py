def canWinNim(n: int) -> bool:
    return n % 4 != 0

# Test the solution with different examples
print(canWinNim(4))  # Output: False
print(canWinNim(5))  # Output: True
print(canWinNim(7))  # Output: True