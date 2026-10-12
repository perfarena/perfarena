def increasing_triplet(nums):
    Max1 = float('inf')
    Max2 = float('inf')
    
    for num in nums:
        if num <= Max1:
            Max1 = num
        elif num <= Max2:
            Max2 = num
        else:
            return True
    
    return False