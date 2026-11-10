def increasing_triplet(nums):
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

# Test the function with a sample array
nums = [2, 1, 5, 0, 4, 6]
print(increasing_triplet(nums))  # Output should be True