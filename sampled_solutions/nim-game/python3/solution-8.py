def canWinGame(n):
    return n % 4 != 0

# Test the function with examples
print(canWinGame(1))  # Output: True
print(canWinGame(4))  # Output: False
print(canWinGame(7))  # Output: True