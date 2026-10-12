for i in range(len(nums)):
      if nums[i] == target or nums[i] > target:
          return i
  return len(nums)