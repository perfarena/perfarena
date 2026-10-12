import java.util.*;

class Solution {
    public List<Integer> closestKValues(TreeNode root, double target, int k) {
        PriorityQueue<Pair<Integer, Double>> pq = new PriorityQueue<>(k, new Comparator<Pair<Integer, Double>>() {
            public int compare(Pair<Integer, Double> a, Pair<Integer, Double> b) {
                return Double.compare(b.getValue(), a.getValue());
            }
        });

        Stack<TreeNode> stack = new Stack<>();
        TreeNode curr = root;

        while (curr != null || !stack.isEmpty()) {
            while (curr != null) {
                stack.push(curr);
                curr = curr.left;
            }

            curr = stack.pop();
            double diff = Math.abs(curr.val - target);
            if (pq.size() < k) {
                pq.offer(new Pair<>(curr.val, diff));
            } else {
                if (pq.peek().getValue() > diff) {
                    pq.poll();
                    pq.offer(new Pair<>(curr.val, diff));
                }
            }

            curr = curr.right;
        }

        List<Integer> result = new ArrayList<>();
        while (!pq.isEmpty()) {
            result.add(pq.poll().getKey());
        }

        return result;
    }
}