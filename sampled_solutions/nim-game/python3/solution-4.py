def canWinStones(n):
    return n % 4 != 0

# Test the function with different cases
print(canWinStones(4))  # Output: False (Lose)
print(canWinStones(5))  # Output: True (Win)
print(canWinStones(10))  # Output: True (Win)