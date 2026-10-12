def intersection_set(nums1, nums2):
    set1 = set(nums1)
    set2 = set(nums2)
    
    # Find the intersection using set operations
    result_set = set1 & set2
    
    return list(result_set)