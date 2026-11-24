count = nums.count(0)
next_non_zero = 0

for n in nums:
    if n != 0:
        nums[next_non_zero] = n
        next_non_zero += 1

for i in range(1, count + 1):
    nums[-i] = 0