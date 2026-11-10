public void merge(int[] nums1, int m, int[] nums2, int n) {
    int[] nums1Copy = new int[m];
    System.arraycopy(nums1, 0, nums1Copy, 0, m);

    int pointer1 = 0, pointer2 = 0;
    for (int i = 0; i < m + n; i++) {
        if (pointer2 >= n || (pointer1 < m && nums1Copy[pointer1] <= nums2[pointer2])) {
            nums1[i] = nums1Copy[pointer1];
            pointer1++;
        } else {
            nums1[i] = nums2[pointer2];
            pointer2++;
        }
    }
}