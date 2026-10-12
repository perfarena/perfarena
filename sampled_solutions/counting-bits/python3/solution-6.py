def countBits(n):
    DP = [0] * (n + 1)
    subtraction = 1
    
    for i in range(1, n + 1):
        if subtraction * 2 == i:
            subtraction = i
        DP[i] = DP[i - subtraction] + 1
    
    return DP

# Test the function with an example
n = 5
print(countBits(n))  # Output: [0, 1, 1, 2, 1, 2]