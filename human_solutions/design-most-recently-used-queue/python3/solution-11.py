def largest_odd_number(nums: str) -> str:
    last = len(nums) - 1
    
    while last >= 0:
        if int(nums[last]) % 2 != 0:
            return nums[:last+1]
        last -= 1
    
    return ""