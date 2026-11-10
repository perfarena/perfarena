# Optimized Solution using Hash Map
def two_sum_optimized(nums, target):
    prev_map = {}
    for i in range(len(nums)):
        difference = target - nums[i]
        if difference in prev_map:
            return [prev_map[difference], i]
        prev_map[nums[i]] = i