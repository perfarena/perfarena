def intersection(nums1, nums2):
    set_nums1 = set(nums1)
    result = []
    
    for num in nums2:
        if num in set_nums1:
            result.append(num)
            set_nums1.remove(num)
    
    return list(result)