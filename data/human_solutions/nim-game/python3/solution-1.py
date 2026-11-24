def canWin(n):
    if n % 4 == 0:
        return False
    else:
        return True

# Test the function with examples
print(canWin(4))  # Output: False
print(canWin(2))  # Output: True