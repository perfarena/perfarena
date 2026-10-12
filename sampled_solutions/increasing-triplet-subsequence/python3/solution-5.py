def increasingTriplet(nums):
    S1 = float('inf')
    S2 = float('inf')
    
    for x in nums:
        if x > S2:
            return True
        if x > S1:
            S2 = min(S2, x)
        S1 = min(S1, x)
    
    return False

# Test the function
nums = [1, 2, 3, 4, 5]
print(increasingTriplet(nums))  # Output: True