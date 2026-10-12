def increasingTriplet(nums):
    if len(nums) < 3:
        return False
    
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