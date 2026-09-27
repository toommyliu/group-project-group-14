import Markdown from 'react-markdown';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import 'katex/dist/katex.min.css';

export function GradingText({ children }: { children: string }) {
  return (
    <div
      className="max-w-[72ch] text-base leading-relaxed wrap-anywhere [&_.katex-display]:overflow-auto [&_.katex-display]:py-1 [&_a]:underline [&_a]:underline-offset-4 [&_blockquote]:border-s-2 [&_blockquote]:ps-4 [&_code]:text-sm [&_ol]:list-decimal [&_ol]:ps-6 [&_pre]:overflow-auto [&_pre]:rounded-md [&_pre]:bg-muted [&_pre]:p-4 [&_ul]:list-disc [&_ul]:ps-6 [&>*+*]:mt-3"
      dir="auto"
    >
      <Markdown
        remarkPlugins={[remarkMath]}
        rehypePlugins={[rehypeKatex]}
        skipHtml
        disallowedElements={['img']}
        components={{ h1: 'p', h2: 'p', h3: 'p', h4: 'p', h5: 'p', h6: 'p' }}
      >
        {children}
      </Markdown>
    </div>
  );
}
