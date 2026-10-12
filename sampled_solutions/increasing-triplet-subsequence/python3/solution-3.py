def increasingTriplet(nums):
    left = float('inf')
    mid = float('inf')
    
    for num in nums:
        if num <= left:
            left = num
        elif num <= mid:
            mid = num
        else:
            return True
    
    return False

# Test the function
nums = [1, 2, 3, 4, 5]
print(increasingTriplet(nums))  # Output: True