// Declare and initialize nums1 and nums2 arrays
int[] nums1 = new int[]{1, 2, 3, 0, 0, 0};
int[] nums2 = new int[]{2, 5, 6};

// Declare and initialize lengths of nums1 and nums2
int m = nums1.length - nums2.length;
int n = nums2.length;

// Initialize variables i, j, and k
int i = m - 1, j = n - 1, k = m + n - 1;

// Merge nums2 into nums1
while (i >= 0 && j >= 0) {
    if (nums1[i] < nums2[j]) {
        nums1[k--] = nums2[j--];
    } else {
        nums1[k--] = nums1[i--];
    }
}

// Handle case when nums1 has no elements
while (j >= 0) {
    nums1[k--] = nums2[j--];
}

// Print the merged and sorted nums1 array
for (int p = 0; p < nums1.length; p++) {
    System.out.print(nums1[p] + " ");
}