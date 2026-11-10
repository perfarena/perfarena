def increasingTriplet(nums):
    C1 = float('inf')
    C2 = float('inf')
    
    for num in nums:
        if num <= C1:
            C1 = num
        elif num <= C2:
            C2 = num
        else:
            return True
    
    return False