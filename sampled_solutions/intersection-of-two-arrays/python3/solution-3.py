def intersection(nums1, nums2):
    nums1_set = set(nums1)
    result = []
    
    for num in nums1:
        if num in nums2:
            result.append(num)
    
    return list(set(result))