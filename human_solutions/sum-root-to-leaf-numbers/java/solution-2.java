public class Solution {
    int totalSum = 0;

    public int sumNumbers(TreeNode root) {
        if (root == null) {
            return 0;
        }
        preOrder(root, 0);
        return totalSum;
    }

    public void preOrder(TreeNode root, int sum) {
        if (root == null) {
            return;
        }

        sum = sum * 10 + root.val;

        if (root.left == null && root.right == null) {
            totalSum += sum;
            return;
        }

        preOrder(root.left, sum);
        preOrder(root.right, sum);
    }
}