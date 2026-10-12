def determine_sign(nums):
    current = 1
    for x in nums:
        if x > 0:
            pass
        elif x == 0:
            current = 0
        else:
            current = -1
    return current