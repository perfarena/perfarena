def increasing_triplet(nums):
    i = j = float('inf')
    
    for k in nums:
        if k <= i:
            i = k
        elif k <= j:
            j = k
        else:
            return True if j < k else False
    
    return False

# Test the function
nums = [1, 2, 3, 4, 5]
print(increasing_triplet(nums))  # Output: True