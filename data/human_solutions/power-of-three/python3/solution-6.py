import math

def isPowerOfThree(n: int) -> bool:
    if n <= 0:
        return False
    return math.log10(n) / math.log10(3) % 1 == 0

# Test the function
print(isPowerOfThree(27))  # Output: True
print(isPowerOfThree(0))   # Output: False
print(isPowerOfThree(1))   # Output: True