class Solution {
    public int sumNumbers(TreeNode root) {
        if (root == null) {
            return 0;
        }

        Stack<Pair<TreeNode, Integer>> stack = new Stack<>();
        stack.push(new Pair<>(root, 0));
        int rootToLeafSum = 0;

        while (!stack.isEmpty()) {
            Pair<TreeNode, Integer> pair = stack.pop();
            TreeNode node = pair.getKey();
            int currentNumber = pair.getValue() * 10 + node.val;

            if (node.left == null && node.right == null) {
                rootToLeafSum += currentNumber;
            } else {
                if (node.right != null) {
                    stack.push(new Pair<>(node.right, currentNumber));
                }
                if (node.left != null) {
                    stack.push(new Pair<>(node.left, currentNumber));
                }
            }
        }

        return rootToLeafSum;
    }
}