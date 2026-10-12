public class MoveZeros {
    public static void main(String[] args) {
        int[] nums = {1, 0, 2, 0, 4, 5, 0, 7};

        int idx = 0;
        for (int i = 0; i < nums.length; i++) {
            if (nums[i] != 0) {
                nums[idx] = nums[i];
                idx++;
            }
        }

        for (int j = idx; j < nums.length; j++) {
            nums[j] = 0;
        }

        for (int k = 0; k < nums.length; k++) {
            System.out.print(nums[k] + " ");
        }
    }
}