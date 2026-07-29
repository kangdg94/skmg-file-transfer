관련 A1 시리얼번호 WRBA1M10KRDWA0925E00624 (2026-07-29 13:23, 13:32 분경), MWPA1M10KRDWA0425Z00469 (2026-07-29 13:18 분경) 입니다.
2026-07-29 13:38:52.004 ERROR [dispatcherServlet].log               175 : Servlet.service() for servlet [dispatcherServlet] in context with path [/api] threw exception

java.lang.ClassCastException: class java.lang.String cannot be cast to class java.lang.Integer (java.lang.String and java.lang.Integer are in module java.base of loader 'bootstrap')

        at com.skmagic.core.component.JwtTokenUtil.getMemberId(JwtTokenUtil.java:40)

        at com.skmagic.core.configuration.TokenAuthenticationFilter.doFilterInternal(TokenAuthenticationFilter.java:175)

        at org.springframework.web.filter.OncePerRequestFilter.doFilter(OncePerRequestFilter.java:116)

        at org.springframework.security.web.FilterChainProxy$VirtualFilterChain.doFilter(FilterChainProxy.java:374)

        at org.springframework.web.filter.CorsFilter.doFilterInternal(CorsFilter.java:91)

        at org.springframework.web.filter.OncePerRequestFilter.doFilter(OncePerRequestFilter.java:116)

        at org.springframework.security.web.FilterChainProxy$VirtualFilterChain.doFilter(FilterChainProxy.java:374)

        at org.springframework.security.web.context.SecurityContextHolderFilter.doFilter(SecurityContextHolderFilter.java:82)

        at org.springframework.security.web.context.SecurityContextHolderFilter.doFilter(SecurityContextHolderFilter.java:69)

        at org.springframework.security.web.FilterChainProxy$VirtualFilterChain.doFilter(FilterChainProxy.java:374)

        at org.springframework.security.web.context.request.async.WebAsyncManagerIntegrationFilter.doFilterInternal(WebAsyncManagerIntegrationFilter.java:62)

        at org.springframework.web.filter.OncePerRequestFilter.doFilter(OncePerRequestFilter.java:116)

        at org.springframework.security.web.FilterChainProxy$VirtualFilterChain.doFilter(FilterChainProxy.java:374)

        at org.springframework.security.web.session.DisableEncodeUrlFilter.doFilterInternal(DisableEncodeUrlFilter.java:42)

        at org.springframework.web.filter.OncePerRequestFilter.doFilter(OncePerRequestFilter.java:116)

        at org.springframework.security.web.FilterChainProxy$VirtualFilterChain.doFilter(FilterChainProxy.java:374)

        at org.springframework.security.web.FilterChainProxy.doFilterInternal(FilterChainProxy.java:233)

        at org.springframework.security.web.FilterChainProxy.doFilter(FilterChainProxy.java:191)

        at org.springframework.web.filter.CompositeFilter$VirtualFilterChain.doFilter(CompositeFilter.java:113)

        at org.springframework.web.servlet.handler.HandlerMappingIntrospector.lambda$createCacheFilter$3(HandlerMappingIntrospector.java:195)

        at org.springframework.web.filter.CompositeFilter$VirtualFilterChain.doFilter(CompositeFilter.java:113)

        at org.springframework.web.filter.CompositeFilter.doFilter(CompositeFilter.java:74)

        at org.springframework.security.config.annotation.web.configuration.WebMvcSecurityConfiguration$CompositeFilterChainProxy.doFilter(WebMvcSecurityConfiguration.java:230)

        at org.springframework.web.filter.DelegatingFilterProxy.invokeDelegate(DelegatingFilterProxy.java:352)

        at org.springframework.web.filter.DelegatingFilterProxy.doFilter(DelegatingFilterProxy.java:268)

        at org.apache.catalina.core.ApplicationFilterChain.internalDoFilter(ApplicationFilterChain.java:174)

        at org.apache.catalina.core.ApplicationFilterChain.doFilter(ApplicationFilterChain.java:149)

        at org.springframework.web.filter.RequestContextFilter.doFilterInternal(RequestContextFilter.java:100)

        at org.springframework.web.filter.OncePerRequestFilter.doFilter(OncePerRequestFilter.java:116)

        at org.apache.catalina.core.ApplicationFilterChain.internalDoFilter(ApplicationFilterChain.java:174)

        at org.apache.catalina.core.ApplicationFilterChain.doFilter(ApplicationFilterChain.java:149)

        at org.springframework.web.filter.FormContentFilter.doFilterInternal(FormContentFilter.java:93)

        at org.springframework.web.filter.OncePerRequestFilter.doFilter(OncePerRequestFilter.java:116)

        at org.apache.catalina.core.ApplicationFilterChain.internalDoFilter(ApplicationFilterChain.java:174)

        at org.apache.catalina.core.ApplicationFilterChain.doFilter(ApplicationFilterChain.java:149)

        at org.springframework.web.filter.CharacterEncodingFilter.doFilterInternal(CharacterEncodingFilter.java:201)

        at org.springframework.web.filter.OncePerRequestFilter.doFilter(OncePerRequestFilter.java:116)

        at org.apache.catalina.core.ApplicationFilterChain.internalDoFilter(ApplicationFilterChain.java:174)

        at org.apache.catalina.core.ApplicationFilterChain.doFilter(ApplicationFilterChain.java:149)

        at org.apache.catalina.core.StandardWrapperValve.invoke(StandardWrapperValve.java:167)

        at org.apache.catalina.core.StandardContextValve.invoke(StandardContextValve.java:90)

        at org.apache.catalina.authenticator.AuthenticatorBase.invoke(AuthenticatorBase.java:482)

        at org.apache.catalina.core.StandardHostValve.invoke(StandardHostValve.java:115)

        at org.apache.catalina.valves.ErrorReportValve.invoke(ErrorReportValve.java:93)

        at org.apache.catalina.core.StandardEngineValve.invoke(StandardEngineValve.java:74)

        at org.apache.catalina.connector.CoyoteAdapter.service(CoyoteAdapter.java:344)

        at org.apache.coyote.http11.Http11Processor.service(Http11Processor.java:391)

        at org.apache.coyote.AbstractProcessorLight.process(AbstractProcessorLight.java:63)

        at org.apache.coyote.AbstractProtocol$ConnectionHandler.process(AbstractProtocol.java:896)

        at org.apache.tomcat.util.net.NioEndpoint$SocketProcessor.doRun(NioEndpoint.java:1744)

        at org.apache.tomcat.util.net.SocketProcessorBase.run(SocketProcessorBase.java:52)

        at org.apache.tomcat.util.threads.ThreadPoolExecutor.runWorker(ThreadPoolExecutor.java:1191)

        at org.apache.tomcat.util.threads.ThreadPoolExecutor$Worker.run(ThreadPoolExecutor.java:659)

        at org.apache.tomcat.util.threads.TaskThread$WrappingRunnable.run(TaskThread.java:63)

        at java.base/java.lang.Thread.run(Thread.java:840)