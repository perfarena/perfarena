def canWinNim(n):
    return n % 4 != 0

# Test the function with different values of n
print(canWinNim(1))  # Output: True
print(canWinNim(4))  # Output: False
print(canWinNim(7))  # Output: True