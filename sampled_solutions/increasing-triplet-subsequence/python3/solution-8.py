min_val = -1
mid_val = -1

for num in nums:
    if min_val == -1 or num < min_val:
        min_val = num
    elif num > min_val and (mid_val == -1 or num < mid_val):
        mid_val = num
    elif mid_val != -1 and num > mid_val:
        return True

return False