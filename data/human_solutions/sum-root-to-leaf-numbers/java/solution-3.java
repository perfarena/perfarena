public int sumRootToLeaf(TreeNode root, int value) {
    if (root == null) {
        return 0;
    }
    value = value * 10 + root.val;
    if (root.left == null && root.right == null) {
        return value;
    }
    return sumRootToLeaf(root.left, value) + sumRootToLeaf(root.right, value);
}